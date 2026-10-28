"""Deterministic business rules from docs/policy.md. No model, no tool calls: plain data in, decision out."""

import json
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app import minimum_info
from app.contracts import TOOLS, validator

CENT = Decimal("0.01")
WRITES = ("update_contact_details", "update_coverage", "terminate_policy",
          "create_claim", "pay_claim", "record_claim_outcome")
PROPOSABLE = ("update_coverage", "terminate_policy", "pay_claim")
PRESCRIPTION_SERVICES = ("diagnostic_test", "physiotherapy")
# Paid, or pending: payment_failed can still be paid (domain allows pay_claim on it), so it blocks a second record.
DUPLICATE_STATUSES = ("registered", "pending_authorization", "payment_failed", "paid")
AUTH_REQUIRED, BLOCKED = "AUTH_REQUIRED", "BLOCKED"


def money(value):
    return f"{Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP):.2f}"


def calculate_reimbursement(expense, policy):
    """Policy formula: 80% of (expense - deductible), half-up to cents, capped by the remaining limit."""
    terms = policy["financial_terms"]
    base = max(Decimal("0"), Decimal(expense) - Decimal(terms["deductible"]))
    theoretical = (base * Decimal("0.8")).quantize(CENT, rounding=ROUND_HALF_UP)
    final = min(theoretical, Decimal(terms["remaining_limit"]))
    return {"expense": money(expense), "deductible": terms["deductible"], "eligible_base": money(base),
            "theoretical": money(theoretical), "remaining_limit": terms["remaining_limit"], "final": money(final),
            "requires_authorization": final > Decimal(policy["authorization_thresholds"]["reimbursement"])}


def find_duplicate(claims, provider_id, invoice_number, invoice_date):
    """Duplicate key: provider ID + invoice number + invoice-date year, against paid or pending records."""
    for claim in claims:
        invoice = claim["invoice"]
        if (claim["status"] in DUPLICATE_STATUSES and invoice["provider_id"] == provider_id
                and invoice["invoice_number"] == invoice_number and invoice["invoice_date"][:4] == invoice_date[:4]):
            return claim
    return None


def created_claims(wf):
    return [e["result"]["data"]["claim_id"] for e in wf["effects"] if e["tool"] == "create_claim"]


def valid_arguments(name, args, run_id):
    return validator("tools.schema.json", TOOLS[name][0] + "Input").is_valid({**args, "run_id": run_id})


def _decimal(value):
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return None


def _authorized(name, args, approved):
    if approved and approved.get("tool") == name and approved.get("arguments") == args:
        return None
    proposal = json.dumps({"tool": name, "arguments": args})
    return AUTH_REQUIRED, (f"{name} with these values requires insurer authorization. Do not call it: return "
                           f"agent_state=awaiting_authorization with proposal {proposal} plus reason and evidence_refs.")


def check_tool_call(name, args, state, wf, approved=None):
    """None if the harness allows the call, else (code, message). Format errors are left to the tool itself."""
    if name not in WRITES or not valid_arguments(name, args, wf["run_id"]):
        return None
    if args["policy_id"] != wf["facts"]["policy_id"]:
        return BLOCKED, f"Only the policy of this request ({wf['facts']['policy_id']}) may be changed."
    policy = state["policies"].get(args["policy_id"])
    if policy is None:
        return None  # The tool reports NOT_FOUND.
    if name in ("pay_claim", "record_claim_outcome") and args["claim_id"] not in created_claims(wf):
        return BLOCKED, "Only a reimbursement record created in this request can be paid or updated."
    if name == "record_claim_outcome":
        return None
    if policy["status"] != "active":
        return BLOCKED, (f"Policy is {policy['status']}: close without execution (request_outcome "
                         f"{'no_change' if policy['status'] == 'terminated' else 'inactive_policy'}).")

    if name == "update_contact_details":
        unsupported = minimum_info.unsupported_contact_values(args["changes"], wf["facts"])
        if unsupported:
            return BLOCKED, (f"{unsupported} do not appear in the request or the reply: never supply contact values "
                             "yourself. Ask for them (once) or close with insufficient_information.")
        return None

    if name == "update_coverage":
        terms, thresholds = policy["financial_terms"], policy["authorization_thresholds"]
        changed = {field: value for field, value in args["changes"].items() if _decimal(value) != _decimal(terms[field])}
        for field, value in changed.items():
            if _decimal(value) < _decimal(terms[field]):
                return BLOCKED, f"Decreasing {field} is not supported: close with request_outcome invalid_input."
        if any(_decimal(value) > _decimal(thresholds[field]) for field, value in changed.items()):
            return _authorized(name, args, approved)
        return None
    if name == "terminate_policy":
        return _authorized(name, args, approved)

    if name == "create_claim":
        facts, invoice, service = wf["facts"], args["invoice"], args["service_category"]
        if service in policy["excluded_services"]:
            return BLOCKED, f"{service} is excluded: close with not_covered and register no record."
        if args["patient_name"] not in {person["name"] for person in policy["insured_people"]}:
            return BLOCKED, "The patient is not insured on this policy: close with not_insured."
        period = policy["coverage_period"]
        if not period["start"] <= invoice["service_date"] <= period["end"]:
            return BLOCKED, "The service date is outside the coverage period: close with outside_coverage_period."
        gaps = minimum_info.missing(facts)
        if service in PRESCRIPTION_SERVICES and not (facts.get("prescription_text") or "").strip() and "prescription" not in gaps:
            gaps.append("prescription")
        if gaps:
            return BLOCKED, (f"Minimum information is missing: {gaps}. Ask for all of it (once) or close with "
                             "insufficient_information; register no record.")
        mismatches = minimum_info.invoice_mismatches(args, facts)
        if mismatches:
            return BLOCKED, f"These values differ from the supplied invoice text; use exactly: {mismatches}."
        if created_claims(wf):
            return BLOCKED, "A reimbursement record was already created for this request."
        duplicate = find_duplicate(state["claims"].values(), invoice["provider_id"],
                                   invoice["invoice_number"], invoice["invoice_date"])
        if duplicate:
            return BLOCKED, (f"Duplicate of existing record {duplicate['claim_id']} ({duplicate['status']}): "
                             "close with duplicate, register and pay nothing.")
        return None

    if name == "pay_claim":
        claim = state["claims"][args["claim_id"]]
        calculation = calculate_reimbursement(claim["invoice"]["expense"], policy)
        if Decimal(calculation["final"]) == 0:
            return BLOCKED, "Calculated reimbursement is 0.00: do not pay; record outcome rejected and close with zero_reimbursement."
        if args["amount"] != calculation["final"]:
            return BLOCKED, f"The amount must be the calculated reimbursement {calculation['final']}."
        if calculation["requires_authorization"]:
            return _authorized(name, args, approved)
    return None


def validate_proposal(proposal, state, wf):
    """None if this is a correct pending authorization, else feedback for the model."""
    if (not isinstance(proposal, dict) or proposal.get("tool") not in PROPOSABLE
            or not isinstance(proposal.get("arguments"), dict)):
        return (f'A proposal must be {{"tool": one of {list(PROPOSABLE)}, "arguments": {{exact tool arguments}}, '
                '"reason": "...", "evidence_refs": [...]}.')
    if not valid_arguments(proposal["tool"], proposal["arguments"], wf["run_id"]):
        return f"The proposal arguments do not match the {proposal['tool']} input schema."
    objection = check_tool_call(proposal["tool"], proposal["arguments"], state, wf)
    if objection is None:
        return "Insurer authorization is not required here: execute the operation directly and report the actual result."
    code, message = objection
    return None if code == AUTH_REQUIRED else message

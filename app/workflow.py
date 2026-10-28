"""Per-ticket workflow state for stop/resume, and the harness check of the model's final answer."""

import json
import re

from app import minimum_info, rules
from app.contracts import ROOT, atomic_json

DIRECTORY = ROOT / ".runtime" / "workflows"
EXPECTED_STATE = {"clarification_reply": "awaiting_clarification", "insurer_decision": "awaiting_authorization"}
STATE_FOR_OUTCOME = {
    "in_progress": "processing",
    "information_requested": "awaiting_clarification",
    "authorization_requested": "awaiting_authorization",
    **dict.fromkeys(("answered", "updated", "terminated", "paid", "no_change"), "completed"),
    **dict.fromkeys(("technical_failure", "handover_required"), "failed"),
    **dict.fromkeys(("duplicate", "not_covered", "inactive_policy", "outside_coverage_period", "not_insured",
                     "out_of_scope", "unclear", "insufficient_information", "insurer_rejected",
                     "zero_reimbursement", "invalid_input"), "closed_without_execution"),
}
# Outcomes that claim an effect, and the successful tool result that must back them.
EFFECT_TOOLS = {"paid": ("pay_claim",), "terminated": ("terminate_policy",),
                "updated": ("update_contact_details", "update_coverage")}


class ReplyNotAdmitted(ValueError):
    """A reply that does not match what the ticket's workflow is waiting for."""


def _path(ticket_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", ticket_id):
        raise ValueError("ticket_id must contain only letters, digits, underscores or hyphens (max 100).")
    return DIRECTORY / f"{ticket_id}.json"


def new(request, run_id, log_path, agent):
    return {"ticket_id": request["ticket_id"], "run_id": run_id, "log_path": str(log_path), "agent": agent,
            "handed_over": False, "state": "processing", "category": None, "facts": dict(request),
            "messages": [{"role": "user", "content": json.dumps(request)}], "clarification_used": False,
            "proposal": None, "effects": [], "format_errors": {}}


def save(wf):
    atomic_json(_path(wf["ticket_id"]), wf)


def load(ticket_id):
    path = _path(ticket_id)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def merge_reply(facts, reply):
    """Add what the reply supplies; never replace a fact from the original request."""
    merged = {**facts, "clarification_text": reply["text"]}
    for field in ("invoice_text", "prescription_text"):
        added = (reply.get(field) or "").strip()
        if added:
            original = (merged.get(field) or "").strip()
            merged[field] = f"{original}\n{added}" if original else added
    return merged


def check_minimum_information(final, wf):
    """A reimbursement asks for exactly what the matrix says is missing, once; never for an excluded service."""
    if (final["request_category"] != "reimbursement_request"
            or final["request_outcome"] not in ("information_requested", "insufficient_information")):
        return final, None
    facts = wf["facts"]
    if minimum_info.excluded(facts):
        return None, "The service is excluded from cover: close with not_covered without asking for any documents."
    gaps = minimum_info.missing(facts)
    if not gaps:
        return None, "All minimum information is present: do not ask or close; continue the reimbursement."
    wf["missing"] = gaps
    if wf["clarification_used"]:
        return final, None  # Still missing after the one reply: settle() closes with insufficient_information.
    # First pass: ask, even if the model wanted to close, with the question generated from the gaps.
    return {**final, "request_outcome": "information_requested", "question": minimum_info.build_question(gaps)}, None


def settle(final, wf, state):
    """Return (final, None) when the answer may stand, or (None, feedback) to send back to the model."""
    final, feedback = check_minimum_information(dict(final), wf)
    if feedback:
        return None, feedback
    outcome = final["request_outcome"]
    if outcome == "information_requested":
        if final["request_category"] == "unclear":
            final.update(request_outcome="unclear", question=None,
                         reason="No operation could be identified; closed without a question.")
        elif wf["clarification_used"]:
            final.update(request_outcome="insufficient_information", question=None,
                         reason="Required information was still missing after the one clarification.")
        elif not final.get("question"):
            return None, "A clarification needs one question that asks for all missing information."
    elif outcome == "authorization_requested":
        feedback = rules.validate_proposal(final.get("proposal"), state, wf)
        if feedback:
            return None, feedback
    elif outcome in EFFECT_TOOLS:
        backed = any(e["tool"] in EFFECT_TOOLS[outcome] and (e["result"]["data"].get("changed") or e["result"]["data"].get("created"))
                     for e in wf["effects"])
        if not backed:
            return None, f"request_outcome {outcome} is not backed by a successful tool result in this request. Report the actual result."
    outcome = final["request_outcome"]
    final["agent_state"] = STATE_FOR_OUTCOME[outcome]
    if outcome != "information_requested":
        final["question"] = None
    if outcome != "authorization_requested":
        final["proposal"] = None
    return final, None

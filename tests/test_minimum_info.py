"""Minimum-information matrix in code: gaps, generated question, and the harness checks that use them."""

import json

import pytest

from app import minimum_info, rules, workflow
from app.contracts import ROOT

TRAINING = ROOT / "examples" / "training"
STATE = json.loads((ROOT / "data" / "initial.json").read_text(encoding="utf-8"))


def ticket(name, **changes):
    return {**json.loads((TRAINING / f"{name}.json").read_text(encoding="utf-8")), **changes}


def wf_for(facts, clarification_used=False):
    return {"run_id": "RUN-test", "facts": facts, "effects": [], "clarification_used": clarification_used}


def answer(outcome, question=None):
    return {"request_category": "reimbursement_request", "agent_state": "processing", "request_outcome": outcome,
            "reason": "r", "message": "m", "question": question, "proposal": None}


@pytest.mark.parametrize("name, gaps", [
    ("T08", []), ("T09", ["prescription"]), ("T10", []), ("T11", []),
    ("T12", []), ("T13", []), ("T14", []), ("T15", []),
])
def test_training_tickets(name, gaps):
    assert minimum_info.missing(ticket(name)) == gaps


def test_invoice_is_parsed_from_text():
    invoice = minimum_info.parse_invoice(ticket("T10")["invoice_text"])
    assert invoice == {"provider_id": "PROVIDER-002", "invoice_number": "INV-2026-010", "invoice_date": "2026-12-31",
                       "service_date": "2026-12-31", "patient": "Taylor Example", "service": "physiotherapy",
                       "amount": "1925.00", "paid": "PAID IN FULL"}


def test_excluded_service_needs_nothing():
    assert minimum_info.excluded(ticket("T14"))
    assert minimum_info.missing(ticket("T14")) == []


def test_no_invoice_asks_for_the_whole_invoice():
    assert minimum_info.missing(ticket("T08", invoice_text=None)) == ["invoice"]


def test_unpaid_invoice_is_not_enough():
    text = ticket("T08")["invoice_text"].replace("PAID IN FULL", "OUTSTANDING")
    assert minimum_info.missing(ticket("T08", invoice_text=text)) == ["paid"]


def test_all_missing_invoice_fields_are_listed_together():
    text = "\n".join(line for line in ticket("T08")["invoice_text"].splitlines()
                     if not line.startswith(("Invoice date", "Amount")))
    assert minimum_info.missing(ticket("T08", invoice_text=text)) == ["invoice_date", "amount"]


def test_prescription_must_name_patient_and_service():
    other_patient = "Patient: Jamie Example. Dr Avery prescribes physiotherapy sessions for this patient."
    other_service = "Patient: Taylor Example. Dr Avery prescribes a specialist consultation."
    assert minimum_info.missing(ticket("T10", prescription_text=other_patient)) == ["prescription_contents"]
    assert minimum_info.missing(ticket("T10", prescription_text=other_service)) == ["prescription_contents"]


def test_question_asks_for_every_gap_once():
    question = minimum_info.build_question(["invoice_date", "amount", "prescription"])
    assert "the invoice date" in question and "the invoice amount in EUR" in question and "prescription" in question


def test_create_claim_arguments_must_match_invoice():
    facts = ticket("T08")
    args = {"policy_id": "POL-001", "patient_name": "Taylor Example", "service_category": "specialist_consultation",
            "invoice": {"provider_id": "PROVIDER-002", "invoice_number": "INV-2026-008", "invoice_date": "2026-10-20",
                        "service_date": "2026-10-20", "expense": "180.00", "currency": "EUR"},
            "evidence_refs": ["request.invoice_text"]}
    assert minimum_info.invoice_mismatches(args, facts) == {}
    assert rules.check_tool_call("create_claim", args, STATE, wf_for(facts)) is None
    wrong = {**args, "invoice": {**args["invoice"], "expense": "1800.00"}}
    assert minimum_info.invoice_mismatches(wrong, facts) == {"expense": "180.00"}
    assert rules.check_tool_call("create_claim", wrong, STATE, wf_for(facts))[0] == rules.BLOCKED


def test_create_claim_blocked_while_information_missing():
    args = {"policy_id": "POL-001", "patient_name": "Taylor Example", "service_category": "diagnostic_test",
            "invoice": {"provider_id": "PROVIDER-002", "invoice_number": "INV-2026-009", "invoice_date": "2026-10-20",
                        "service_date": "2026-10-20", "expense": "300.00", "currency": "EUR"},
            "evidence_refs": ["request.invoice_text"]}
    code, message = rules.check_tool_call("create_claim", args, STATE, wf_for(ticket("T09")))
    assert code == rules.BLOCKED and "prescription" in message


def test_contact_values_must_come_from_the_policyholder():
    address = {"street": "8 Orchard Road", "city": "Turin", "postal_code": "10100", "country": "Italy"}
    facts = ticket("T04")
    assert minimum_info.unsupported_contact_values({"address": address}, facts) == ["address.postal_code"]
    replied = workflow.merge_reply(facts, {"text": "The postal code is 10100."})
    assert minimum_info.unsupported_contact_values({"address": address}, replied) == []
    args = {"policy_id": "POL-001", "changes": {"address": address}}
    assert rules.check_tool_call("update_contact_details", args, STATE, wf_for(facts))[0] == rules.BLOCKED
    assert rules.check_tool_call("update_contact_details", args, STATE, wf_for(replied)) is None


def test_phone_matches_with_spaces():
    facts = ticket("T03", text="Please change my phone number to +39 333 999 8888.")
    assert minimum_info.unsupported_contact_values({"phone": "+393339998888"}, facts) == []


def test_reply_cannot_change_original_invoice_facts():
    facts = ticket("T08")
    reply = {"text": "Corrected invoice.", "invoice_text": facts["invoice_text"].replace("180.00", "980.00")}
    assert minimum_info.conflicts(facts, reply) == ["amount"]
    merged = workflow.merge_reply(facts, reply)
    assert minimum_info.parse_invoice(merged["invoice_text"])["amount"] == "180.00"


def test_settle_replaces_question_with_the_generated_one():
    final, feedback = workflow.settle(answer("information_requested", "Send the doctor's note?"), wf_for(ticket("T09")), STATE)
    assert feedback is None
    assert final["agent_state"] == "awaiting_clarification"
    assert final["question"] == minimum_info.build_question(["prescription"])


def test_settle_asks_before_closing_insufficient():
    final, _ = workflow.settle(answer("insufficient_information"), wf_for(ticket("T09")), STATE)
    assert final["request_outcome"] == "information_requested"


def test_settle_closes_after_the_one_reply():
    final, _ = workflow.settle(answer("information_requested", "Again?"), wf_for(ticket("T09"), True), STATE)
    assert final["request_outcome"] == "insufficient_information" and final["question"] is None


def test_logged_event_kinds_exist_in_the_log_schema():
    import re
    schema = json.loads((ROOT / "contracts" / "evaluation.schema.json").read_text(encoding="utf-8"))
    allowed = schema["properties"]["events"]["items"]["properties"]["kind"]["enum"]
    used = set(re.findall(r'log\.event\(\s*"(\w+)"', (ROOT / "app" / "runtime.py").read_text(encoding="utf-8")))
    assert used <= set(allowed), used - set(allowed)


def test_settle_refuses_questions_that_are_not_needed():
    assert workflow.settle(answer("information_requested", "Q?"), wf_for(ticket("T08")), STATE)[1]
    assert "not_covered" in workflow.settle(answer("information_requested", "Q?"), wf_for(ticket("T14")), STATE)[1]

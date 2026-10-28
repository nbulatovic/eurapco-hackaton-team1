"""Reimbursement-record lifecycle: no record a request created stays open when the request ends.

Real domain store in a temporary folder, a fake domain client and no model."""

import asyncio
import json

import pytest

from app import rules, runtime, workflow
from app.contracts import ROOT
from app.domain import DomainStore, error
from app.evaluation import EvaluationLog

T11 = json.loads((ROOT / "examples" / "training" / "T11.json").read_text(encoding="utf-8"))
INVOICE = {"provider_id": "PROVIDER-002", "invoice_number": "INV-2026-011", "invoice_date": "2026-10-20",
           "service_date": "2026-10-20", "currency": "EUR"}


@pytest.mark.parametrize("request_outcome, status, payment_failed, expected", [
    ("zero_reimbursement", "registered", False, "rejected"),
    ("insurer_rejected", "pending_authorization", False, "rejected"),
    ("not_covered", "registered", False, "not_covered"),
    ("insufficient_information", "registered", False, "incomplete"),
    ("duplicate", "registered", False, "invalid"),
    ("technical_failure", "pending_authorization", True, "payment_failed"),
    ("technical_failure", "registered", False, None),           # left open for the colleague who takes over
    ("handover_required", "registered", False, None),
    ("authorization_requested", "registered", False, None),     # waiting: the record stays open
    ("information_requested", "registered", False, None),
    ("paid", "paid", False, None),                               # already final
    ("zero_reimbursement", "rejected", False, None),             # the agent already recorded it
])
def test_closing_record_outcome(request_outcome, status, payment_failed, expected):
    assert rules.closing_record_outcome(request_outcome, status, payment_failed) == expected


class FakeDomain:
    """Calls the real domain store; can make payments fail like a technical payment error."""

    def __init__(self, store, run_id, fail_payments=False):
        self.store, self.run_id, self.fail_payments = store, run_id, fail_payments

    async def call(self, name, args):
        if name == "pay_claim" and self.fail_payments:
            return error("OPERATION_FAILED", "Simulated payment provider outage.")
        return self.store.call(name, {**args, "run_id": self.run_id})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("EURAPCO_DATA_DIR", str(tmp_path / "domain"))
    monkeypatch.setattr(workflow, "DIRECTORY", tmp_path / "workflows")
    store = DomainStore()
    run_id = store.start_run()
    log = EvaluationLog(T11, run_id, "TEAM-1", tmp_path / "logs")
    log.route("claims")
    wf = workflow.new(T11, run_id, log.path, "claims")
    wf["category"] = "reimbursement_request"
    s = runtime.Session(wf, log)
    s.domain = FakeDomain(store, run_id)
    monkeypatch.setattr(runtime, "DomainClient", lambda *args, **kwargs: s.domain)
    return s


def run(coroutine):
    return asyncio.run(coroutine)


def create(s, expense):
    args = {"policy_id": "POL-001", "patient_name": "Taylor Example", "service_category": "specialist_consultation",
            "invoice": {**INVOICE, "expense": expense}, "evidence_refs": ["request.invoice_text"]}
    s.wf["facts"]["invoice_text"] = s.wf["facts"]["invoice_text"].replace("2000.00", expense)
    result = run(s.call(s.domain, "create_claim", args))
    assert result["ok"], result
    return result["data"]["claim_id"]


def status(s, claim_id):
    return s.state()["claims"][claim_id]["status"]


def close(s, outcome):
    return run(s.finish(s.domain, runtime.closing(s.wf, outcome, "Test closure.", "Test message.")))


def ask_insurer(s, claim_id, amount):
    proposal = {"tool": "pay_claim", "arguments": {"policy_id": "POL-001", "claim_id": claim_id, "amount": amount},
                "reason": "Above EUR 1500.", "evidence_refs": ["request.invoice_text"]}
    final = {**runtime.closing(s.wf, "authorization_requested", "Needs the insurer.", "We asked the insurer."),
             "proposal": proposal}
    run(s.finish(s.domain, final))


def test_zero_reimbursement_record_is_rejected_by_the_harness(session):
    claim_id = create(session, "40.00")
    close(session, "zero_reimbursement")
    assert status(session, claim_id) == "rejected"


def test_orphan_record_gets_the_closing_outcome(session):
    claim_id = create(session, "180.00")
    close(session, "duplicate")
    assert status(session, claim_id) == "invalid"


def test_waiting_for_the_insurer_keeps_the_record_pending(session):
    claim_id = create(session, "2000.00")
    ask_insurer(session, claim_id, "1560.00")
    assert status(session, claim_id) == "pending_authorization"


def test_approval_pays_the_saved_amount(session):
    claim_id = create(session, "2000.00")
    ask_insurer(session, claim_id, "1560.00")
    final = run(session.decide({"decision": "approve"}))
    assert final["request_outcome"] == "paid"
    assert status(session, claim_id) == "paid"
    assert session.state()["policies"]["POL-001"]["financial_terms"]["remaining_limit"] == "1336.00"


def test_rejection_records_rejected_without_payment(session):
    claim_id = create(session, "2000.00")
    ask_insurer(session, claim_id, "1560.00")
    final = run(session.decide({"decision": "reject", "reason": "Not approved."}))
    assert final["request_outcome"] == "insurer_rejected"
    assert status(session, claim_id) == "rejected"
    assert session.state()["claims"][claim_id]["payment"] is None


def test_failed_approved_payment_is_recorded_as_payment_failed(session):
    claim_id = create(session, "2000.00")
    ask_insurer(session, claim_id, "1560.00")
    session.domain.fail_payments = True
    final = run(session.decide({"decision": "approve"}))
    assert final["request_outcome"] == "technical_failure"
    assert status(session, claim_id) == "payment_failed"
    assert session.state()["policies"]["POL-001"]["financial_terms"]["remaining_limit"] == "2896.00"


def test_handover_without_payment_attempt_leaves_the_record_open(session):
    claim_id = create(session, "180.00")
    close(session, "handover_required")
    assert status(session, claim_id) == "registered"


def test_log_stays_valid(session):
    # Tool calls themselves are logged by the real MCP client, which this fake replaces.
    create(session, "40.00")
    close(session, "zero_reimbursement")
    record = EvaluationLog.load(session.log.path).record
    assert (record["agent_state"], record["request_outcome"]) == ("closed_without_execution", "zero_reimbursement")

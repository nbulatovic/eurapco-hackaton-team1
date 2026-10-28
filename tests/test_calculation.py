"""Deterministic reimbursement formula and duplicate key from docs/policy.md, and the harness gates that use them."""

import copy
import json
from decimal import Decimal

import pytest

from app import rules
from app.contracts import ROOT

STATE = json.loads((ROOT / "data" / "initial.json").read_text(encoding="utf-8"))
CLAIMS = list(STATE["claims"].values())


def policy(deductible="50.00", remaining="3000.00"):
    return {"financial_terms": {"deductible": deductible, "remaining_limit": remaining},
            "authorization_thresholds": {"reimbursement": "1500.00"}}


@pytest.mark.parametrize("expense, remaining, final, needs_insurer", [
    ("180.00", "2896.00", "104.00", False),   # policy.md examples with deductible 50
    ("1925.00", "2896.00", "1500.00", False),  # equality with the threshold is autonomous
    ("2000.00", "2896.00", "1560.00", True),
    ("2000.00", "900.00", "900.00", False),    # capped by the remaining limit, then autonomous
])
def test_policy_examples(expense, remaining, final, needs_insurer):
    result = rules.calculate_reimbursement(expense, policy(remaining=remaining))
    assert (result["final"], result["requires_authorization"]) == (final, needs_insurer)


def test_breakdown_is_reported():
    assert rules.calculate_reimbursement("2000.00", policy(remaining="900.00")) == {
        "expense": "2000.00", "deductible": "50.00", "eligible_base": "1950.00", "theoretical": "1560.00",
        "remaining_limit": "900.00", "final": "900.00", "requires_authorization": False}


@pytest.mark.parametrize("expense, final", [("0.00", "0.00"), ("40.00", "0.00"), ("50.00", "0.00"),
                                            ("50.01", "0.01"), ("50.03", "0.02"), ("50.06", "0.05")])
def test_deductible_floor_and_rounding_to_cents(expense, final):
    assert rules.calculate_reimbursement(expense, policy())["final"] == final


def test_authorization_compares_the_final_amount_strictly():
    assert rules.calculate_reimbursement("1925.02", policy())["final"] == "1500.02"
    assert rules.calculate_reimbursement("1925.02", policy())["requires_authorization"] is True
    assert rules.calculate_reimbursement("5000.00", policy(remaining="1500.00"))["requires_authorization"] is False


def test_current_deductible_is_used_after_a_coverage_change():
    assert rules.calculate_reimbursement("180.00", policy(deductible="200.00"))["final"] == "0.00"
    assert rules.calculate_reimbursement("2000.00", policy(deductible="500.00"))["final"] == "1200.00"


def test_no_remaining_limit_means_zero():
    assert rules.calculate_reimbursement("2000.00", policy(remaining="0.00"))["final"] == "0.00"


def test_invariants_over_many_amounts():
    for cents in range(0, 600001, 137):
        expense = Decimal(cents) / 100
        for remaining in ("0.00", "900.00", "2896.00"):
            result = rules.calculate_reimbursement(f"{expense:.2f}", policy(remaining=remaining))
            final = Decimal(result["final"])
            assert result["final"] == f"{final:.2f}"
            assert Decimal("0") <= final <= Decimal(remaining)
            assert final <= expense * Decimal("0.8")
            assert result["requires_authorization"] == (final > Decimal("1500"))


# Duplicate key: provider ID + invoice number + invoice-date year, against paid or pending records.

def test_t13_matches_the_historical_record():
    assert rules.find_duplicate(CLAIMS, "PROVIDER-001", "INV-2026-001", "2026-10-02")["claim_id"] == "CL-H001"


@pytest.mark.parametrize("provider_id, invoice_number, invoice_date", [
    ("PROVIDER-001", "INV-2026-001", "2027-01-15"),  # same invoice number, another year
    ("PROVIDER-009", "INV-2026-001", "2026-10-02"),  # another provider
    ("PROVIDER-001", "INV-2026-099", "2026-10-02"),  # another invoice number
])
def test_any_part_of_the_key_differing_is_not_a_duplicate(provider_id, invoice_number, invoice_date):
    assert rules.find_duplicate(CLAIMS, provider_id, invoice_number, invoice_date) is None


def test_same_year_with_another_date_is_a_duplicate():
    assert rules.find_duplicate(CLAIMS, "PROVIDER-001", "INV-2026-001", "2026-01-31") is not None


@pytest.mark.parametrize("status, duplicate", [
    ("paid", True), ("registered", True), ("pending_authorization", True), ("payment_failed", True),
    ("rejected", False), ("not_covered", False), ("incomplete", False), ("invalid", False),
])
def test_only_paid_or_pending_records_block(status, duplicate):
    claims = [{**CLAIMS[0], "status": status}]
    assert (rules.find_duplicate(claims, "PROVIDER-001", "INV-2026-001", "2026-10-02") is not None) == duplicate


# The gates: the harness refuses what the formula and the duplicate key forbid.

def claim_args(**invoice):
    return {"policy_id": "POL-001", "patient_name": "Taylor Example", "service_category": "specialist_consultation",
            "invoice": {"provider_id": "PROVIDER-001", "invoice_number": "INV-2026-001", "invoice_date": "2026-10-02",
                        "service_date": "2026-10-01", "expense": "180.00", "currency": "EUR", **invoice},
            "evidence_refs": ["request.invoice_text"]}


def ticket_facts(name):
    return json.loads((ROOT / "examples" / "training" / f"{name}.json").read_text(encoding="utf-8"))


def test_duplicate_registration_is_refused():
    wf = {"run_id": "RUN-test", "facts": ticket_facts("T13"), "effects": []}
    code, message = rules.check_tool_call("create_claim", claim_args(), STATE, wf)
    assert code == rules.BLOCKED and "CL-H001" in message


def state_with_claim(expense, remaining="2896.00"):
    state = copy.deepcopy(STATE)
    state["policies"]["POL-001"]["financial_terms"]["remaining_limit"] = remaining
    state["claims"]["CL-NEW"] = {"claim_id": "CL-NEW", "policy_id": "POL-001", "status": "registered",
                                 "invoice": {"expense": expense}}
    wf = {"run_id": "RUN-test", "facts": {"policy_id": "POL-001"},
          "effects": [{"tool": "create_claim", "arguments": {}, "result": {"ok": True, "data": {"claim_id": "CL-NEW"}}}]}
    return state, wf


def pay(amount):
    return {"policy_id": "POL-001", "claim_id": "CL-NEW", "amount": amount}


def test_payment_must_equal_the_calculated_amount():
    state, wf = state_with_claim("180.00")
    assert rules.check_tool_call("pay_claim", pay("104.00"), state, wf) is None
    code, message = rules.check_tool_call("pay_claim", pay("144.00"), state, wf)
    assert code == rules.BLOCKED and "104.00" in message


def test_zero_reimbursement_is_never_paid():
    state, wf = state_with_claim("40.00")
    assert rules.check_tool_call("pay_claim", pay("0.00"), state, wf)[0] == rules.BLOCKED


def test_payment_above_threshold_needs_the_exact_approved_proposal():
    state, wf = state_with_claim("2000.00")
    assert rules.check_tool_call("pay_claim", pay("1560.00"), state, wf)[0] == rules.AUTH_REQUIRED
    approved = {"tool": "pay_claim", "arguments": pay("1560.00")}
    assert rules.check_tool_call("pay_claim", pay("1560.00"), state, wf, approved) is None


def test_capped_payment_is_autonomous():
    state, wf = state_with_claim("2000.00", remaining="900.00")
    assert rules.check_tool_call("pay_claim", pay("900.00"), state, wf) is None


def test_only_records_created_in_this_request_can_be_paid():
    state, wf = state_with_claim("180.00")
    wf["effects"] = []
    assert rules.check_tool_call("pay_claim", pay("104.00"), state, wf)[0] == rules.BLOCKED

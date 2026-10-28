---
name: reimbursement-request
agent: claims
categories: [reimbursement_request]
description: A new medical reimbursement, from the paid invoice (and prescription when required) to payment, proposal or closure.
---

# Reimbursement request

## When to use
- The policyholder asks to be paid back for a medical expense: "reimburse", "pay me back", "here is the paid bill".
- Also when the service is excluded (dental cleaning): it is still a reimbursement_request, closed as not_covered.
- Not for a question about an existing record (claim_enquiry), nor for reopening, appealing or adding to one (out_of_scope_claim_integration).
- Out of scope: splitting an invoice, several patients or services in one request, pre-authorizations and direct payment to a provider.

## Read first
- get_policy: status, insured_people, coverage_period, financial_terms, covered and excluded services.
- invoice_text and prescription_text from the request, and from the clarification reply if there is one.

## Steps
1. Call get_policy.
2. Find the service on the invoice's "Service:" line (the request text only when there is no invoice):
   | Invoice says | service_category |
   |---|---|
   | specialist, consultation, cardiology, dermatology, visit | specialist_consultation |
   | diagnostic, blood test, MRI, X-ray, scan, ultrasound | diagnostic_test |
   | physiotherapy | physiotherapy |
   | dental, cosmetic, medicines | excluded: close not_covered now, ask for nothing |
3. Check, in this order: policy status is active; the invoice patient is one of insured_people (exact name); the service date is inside coverage_period, first and last day included.
4. Check the evidence. Specialist consultation: the paid invoice. Diagnostic test and physiotherapy: the paid invoice and a prescription naming the same patient and the service. If anything is missing, return information_requested: the harness writes the exact question.
5. Call check_duplicate with the invoice's provider ID, invoice number and invoice date. A match closes as duplicate; name the existing claim_id and its status.
6. Call create_claim, copying every value exactly from the invoice: provider_id, invoice_number, invoice_date, service_date, expense (two decimals), currency EUR; patient_name exactly as in insured_people; evidence_refs such as "request.invoice_text", "request.prescription_text", "clarification.prescription_text".
7. Call calculate_reimbursement with the expense and use its "final" amount. Never calculate yourself.
8. Decide on "final":
   - 0.00: do not pay; close with zero_reimbursement.
   - requires_authorization false: pay_claim with exactly "final"; report paid with the payment ID and record ID.
   - requires_authorization true: do not pay; return the proposal below.

## Ask the policyholder (once) when
- The invoice is missing, or lacks any of: provider ID, invoice number, invoice date, service date, patient, service, EUR amount, paid status.
- A diagnostic test or physiotherapy has no prescription, or the prescription does not name the patient and the service.
- Never for the deductible, the remaining limit or anything else get_policy provides. Never for an excluded service.
- Return request_outcome information_requested. The harness replaces your question with one listing exactly the missing items. After the one reply, anything still missing closes as insufficient_information.

## Ask the insurer when
- The final reimbursement is strictly above EUR 1500. Exactly 1500.00 is paid without the insurer.
- Proposal: `{"tool": "pay_claim", "arguments": {"policy_id": "...", "claim_id": "...", "amount": "<final>"}, "reason": "...", "evidence_refs": [...]}`

## Close without execution when
| Condition | request_outcome |
|---|---|
| Dental care, cosmetic treatment or medicines | not_covered |
| Policy not active | inactive_policy |
| Patient not on the policy | not_insured |
| Service date outside the coverage period | outside_coverage_period |
| Same provider ID, invoice number and invoice-date year already paid or pending | duplicate |
| Still missing after the one clarification | insufficient_information |
| Calculated reimbursement 0.00 | zero_reimbursement |

## Explain the amount
State the actual figures from calculate_reimbursement: expense, minus the deductible, times 80 %, and the cap by the remaining limit when it applies. For example: "EUR 2000.00 − 50.00 = 1950.00 × 80 % = 1560.00, capped by your remaining limit of 900.00: EUR 900.00."

## The harness will refuse
- create_claim while information is missing, with values that differ from the invoice text, for a duplicate, or a second time in the same request.
- pay_claim with any amount other than "final", for 0.00, or above EUR 1500 without the insurer's approval.
- Reporting paid without a successful pay_claim. Record outcomes are stored by the harness when the request ends; never call record_claim_outcome yourself.

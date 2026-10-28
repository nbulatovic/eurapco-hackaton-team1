---
name: claim-enquiry
agent: claims
categories: [claim_enquiry]
description: Explain the actual status and payment of an existing reimbursement record.
---

# Claim enquiry

## When to use
- The policyholder asks about an existing record: "What is the status of my claim CL-H001?", "Was my October invoice paid?".
- Not for a new reimbursement (reimbursement_request). Reopening, appealing, adding documents to or changing an existing record is out_of_scope_claim_integration.

## Read first
- claim_id from the request envelope, if present.
- get_claim with policy_id and claim_id; otherwise list_claims with policy_id.

## Steps
1. With a claim_id: call get_claim.
2. Without one: call list_claims and identify the record from what the request says (invoice number, provider, date, amount, patient). Use it only when exactly one record matches.
3. Explain only what the record shows:
   | status | Say |
   |---|---|
   | paid | Paid: the payment amount and payment ID |
   | registered | Received and being processed; not paid yet |
   | pending_authorization | Waiting for the insurer's decision; not paid yet |
   | payment_failed | The payment did not go through; not paid |
   | rejected, not_covered, incomplete, invalid | Closed without payment, with the stored reason |
4. Return completed / answered.

## Ask the policyholder (once) when
- No claim_id is given and no single record can be identified from the request and list_claims. Ask for the claim ID.

## Close without execution when
| Condition | request_outcome |
|---|---|
| The claim ID does not exist, or belongs to another policy (NOT_FOUND, REFERENCE_MISMATCH) | invalid_input |
| Still not identifiable after the one clarification | insufficient_information |

## Never
- Create, pay or update a record during an enquiry.
- Reconstruct a status or amount the record does not show. "Pending" is not paid.

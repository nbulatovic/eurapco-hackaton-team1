---
name: coverage-change
agent: administration
categories: [coverage_change]
description: Increase the annual limit and/or deductible within the catalog, asking the insurer above the thresholds.
---

# Coverage change

## When to use
- The policyholder asks to change the annual reimbursement limit and/or the per-claim deductible.
- Not for premiums, underwriting, effective dates or renewals (out_of_scope_renewal / out_of_scope_other).

## Read first
- get_policy: status, financial_terms (current annual_limit and deductible), allowed_values, authorization_thresholds.

## Steps
1. Call get_policy. A policy that is not active closes as inactive_policy.
2. Read the requested FINAL values. Catalog: annual limit 3000, 5000, 10000 EUR; deductible 50, 100, 200, 500 EUR.
3. Keep only the fields whose final value differs from the current one. If none differ, close as no_change.
4. Any changed field that decreases, or is not in the catalog: close as invalid_input with no tool call. The whole request stays unchanged, even if another field was valid.
5. Compare the final values of the changed fields with the thresholds:
   | Final value | Insurer needed |
   |---|---|
   | annual_limit 10000 | yes (above 5000) |
   | annual_limit 5000 | no (equal is autonomous) |
   | deductible 500 | yes (above 200) |
   | deductible 100 or 200 | no |
6. If any changed field needs the insurer, return one proposal with all changed fields together. Otherwise call update_coverage with all changed fields and report before and after from the result.
7. When explaining, note that a higher deductible means the insured pays more of each claim.

## Ask the policyholder (once) when
- The request does not say the intended final value ("increase my limit" without an amount).
- Never for current values: get_policy provides them.

## Ask the insurer when
- Final annual_limit above 5000 or final deductible above 200.
- Proposal, one for the combined change: `{"tool": "update_coverage", "arguments": {"policy_id": "...", "changes": {"annual_limit": "10000.00", "deductible": "500.00"}}, "reason": "...", "evidence_refs": ["request.text"]}`

## Close without execution when
| Condition | request_outcome |
|---|---|
| Policy not active | inactive_policy |
| Final values equal the current ones | no_change |
| A decrease or a value outside the catalog | invalid_input |
| Still no final value after the one clarification | insufficient_information |

## The harness will refuse
- update_coverage with a decrease, or above a threshold without the insurer's approval of exactly that proposal.
- Splitting a combined change into two writes. A rejected or inadmissible combined change writes nothing.

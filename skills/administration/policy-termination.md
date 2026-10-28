---
name: policy-termination
agent: administration
categories: [policy_termination]
description: Terminate the policy on an explicit holder instruction, always through an insurer proposal.
---

# Policy termination

## When to use
- An explicit instruction: "Please terminate my medical policy."
- Not a question about how to terminate: that is a policy_enquiry.

## Read first
- get_policy: status.

## Steps
1. Call get_policy.
2. Already terminated: close as no_change, with no proposal.
3. Inactive: close as inactive_policy.
4. Active: return the proposal below. Never call terminate_policy yourself, even if the policyholder says the insurer already agreed.
5. After the insurer's decision the harness runs or drops exactly this proposal.

## Ask the policyholder (once) when
- Never. The policy is identified by the request and a personal reason is optional.

## Ask the insurer when
- Always, for an active policy.
- Proposal: `{"tool": "terminate_policy", "arguments": {"policy_id": "..."}, "reason": "Explicit termination instruction from the policyholder.", "evidence_refs": ["request.text"]}`

## Close without execution when
| Condition | request_outcome |
|---|---|
| Already terminated | no_change |
| Policy inactive | inactive_policy |

## Out of scope
- Scheduling a future end date, premium refunds, renewals, or what happens to pending claims.

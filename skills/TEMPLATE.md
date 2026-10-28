---
# Skill frontmatter schema (copy this file to skills/<agent>/<name>.md; files here at the root are not loaded)
name: reimbursement-request            # kebab-case, same as the file name
agent: claims                          # administration | claims
categories: [reimbursement_request]    # subset of contracts/vocabulary.json request_categories[agent]
description: One line saying when this procedure applies.
---

# <Procedure title>

## When to use
- The request categories / phrasings this covers, and look-alikes it does NOT cover
  (e.g. "how do I terminate?" is a policy_enquiry, not a policy_termination).

## Read first
- Tools to call before deciding or asking (e.g. get_policy, list_claims, check_duplicate).
- Facts to take from the request envelope (policy_id, claim_id, invoice_text, prescription_text).

## Steps
1. ...
2. ...
3. ...

## Ask the policyholder (once) when
- Exactly which missing items warrant the single clarification (docs/minimum-information.md).
- Ask for ALL of them in one question. Never ask for what tools already provide.

## Ask the insurer when
- The threshold or rule that needs authorization, and the exact proposal to return:
  `{"tool": "...", "arguments": {...}, "reason": "...", "evidence_refs": [...]}`

## Close without execution when
| Condition | agent_state | request_outcome |
|---|---|---|
| ... | closed_without_execution | ... |

## Never
- Effects the harness will refuse anyway, and claims that would not match the tool results.

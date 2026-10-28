# What we changed in the harness

Team 1 · Eurapco hackathon · 28 Oct 2026

For each task in the brief: what the starter kit gave us, what we built on top of it, and what is still open.
Status reflects the code on `main` after pull requests #1–#4 and our runs of the 15 training tickets, not a scored evaluation.

## In one sentence

**Code decides what is allowed; the model interprets the request and writes the answer.** The starter kit trusted the model with every rule. We moved routing, missing-information checks, business rules, calculation, duplicates, the record lifecycle and human replies into the harness, and gave each agent one skill per operation.

## How a request flows

```mermaid
flowchart TD
    E[Event] -->|request| R[Router: TypeSafe, Claude fallback]
    E -->|clarification_reply / insurer_decision| W[(Workflow file per ticket<br/>same run ID, same log)]
    W --> A
    R --> A[Agent: prompt + skill for the category]
    A -->|tool call| G{Write gate<br/>rules.check_tool_call}
    G -->|allowed| T[9 MCP domain tools<br/>+ calculate_reimbursement, check_duplicate]
    G -->|refused| A
    T --> A
    A -->|answer| S{Settle: minimum info,<br/>claimed effects, state}
    S -->|missing info| Q[One generated question<br/>awaiting_clarification]
    S -->|needs insurer| P[Saved proposal<br/>awaiting_authorization]
    S -->|done| C[Close open records, log status + snapshot]
    Q --> W
    P --> W
```

| Layer | Who decides | Where |
|---|---|---|
| Which agent | Router (TypeSafe, then Claude) | `app/router.py` |
| What is missing, and the question | Code | `app/minimum_info.py` |
| How to do the operation | Model, guided by a skill | `skills/`, `app/skills.py` |
| Whether a write is allowed | Code | `app/rules.py` |
| Amount and duplicates | Code | `app/rules.py` (local tools) |
| What happens after an insurer decision | Code, without the model | `app/runtime.py` |
| What is reported as done | Tool results, not the model's claim | `app/workflow.py` |

## Results on the training tickets

Latest run of each initial request, read from `logs/`. Every run follows the policy's expected outcome.

| Ticket | Request | Agent · category | Result | Evidence in the log |
|---|---|---|---|---|
| T01 | Physiotherapy covered? deductible, remaining limit | admin · policy_enquiry | completed · answered | `get_policy` only, no writes |
| T02 | Status of claim CL-H001 | claims · claim_enquiry | completed · answered | read only |
| T03 | Change phone number | admin · contact_update | completed · updated | phone +393331234567 → +393339998888 in the snapshot |
| T04 | New address without postal code | admin · contact_update | awaiting_clarification → reply → completed · updated | 1 clarification round; same run ID; repeated replies refused |
| T05 | Limit 5000, deductible 200 | admin · coverage_change | completed · updated | at the thresholds, so no insurer needed |
| T06 | Limit 10000, deductible 500, together | admin · coverage_change | awaiting_authorization | one combined proposal, nothing written |
| T07 | Terminate my policy | admin · policy_termination | awaiting_authorization | proposal, `terminate_policy` not called |
| T08 | Specialist consultation, EUR 180 | claims · reimbursement_request | completed · paid 104.00 | calculated in code: (180 − 50) × 80 % |
| T09 | Blood test, no prescription | claims · reimbursement_request | awaiting_clarification | asks for the prescription only |
| T10 | Physiotherapy on the last day of cover | claims · reimbursement_request | completed · paid | last day counted as covered |
| T11 | Specialist, EUR 2000 | claims · reimbursement_request | awaiting_authorization | final 1560.00 > 1500; record created and pending |
| T12 | MRI EUR 2000, POL-002 with 900 left | claims · reimbursement_request | completed · paid 900.00 | capped by remaining limit, so autonomous |
| T13 | Cardiology invoice already paid | claims · reimbursement_request | closed · duplicate | `check_duplicate` → CL-H001, no record, no payment |
| T14 | Dental cleaning, no documents | claims · reimbursement_request | closed · not_covered | no documents requested |
| T15 | Specialist on POL-003 | claims · reimbursement_request | closed · inactive_policy | no record created |

Insurer approve and reject (T06, T07, T11) are covered by the automated lifecycle tests on a real domain store; live runs of those replies are in the open items below.

## Status by task

| # | Task | Status | Main change |
|---|---|---|---|
| 1 | [Router and classification](#1-router-and-classification) | ✅ Done | TypeSafe classifier with Claude fallback; handover between agents; category validated |
| 2 | [Minimum information, asked once](#2-minimum-information-asked-once) | ✅ Done | Matrix, invoice parser and generated question in code |
| 3 | [Workflow state and stop/resume](#3-workflow-state-and-stopresume) | ✅ Done | Per-ticket workflow file; replies resume the same run and log |
| 4 | [Admissibility, authority, proposal integrity](#4-admissibility-authority-and-proposal-integrity) | ✅ Done | Every write goes through a rule gate; approval runs the exact saved proposal |
| 5 | [Calculation and duplicate prevention](#5-calculation-and-duplicate-prevention) | ✅ Done | Deterministic formula and duplicate key in code, covered by 34 tests |
| 6 | [Reimbursement-record lifecycle](#6-reimbursement-record-lifecycle) | ✅ Done | Every record ends in its actual outcome; state diagram and 20 tests |
| 7 | [Skills per agent](#7-skills-per-agent) | ✅ Done | Six skills, one per operation, loaded by category |
| 8 | [Logger integration](#8-logger-integration) | ✅ Done | Routing, status, human rounds, resumes, blocks and gaps all logged |
| 9 | [Actual results, working enquiries](#9-actual-results-and-working-enquiries) | ✅ Done | Claimed effects must be backed by tool results; all 15 tickets run with the expected outcome |

---

## 1. Router and classification

> Improve the router and classification, and organize your harness around the common scope.

**Starter kit:** one regular expression. Any of "claim", "invoice", "reimburse" sends the request to Claims, everything else to Administration. The agent's model then picks the category on its own.

**What we did**
- **Classifier:** the router asks TypeSafe (`system_one` with one `Choice`) to pick between the two agents. Each agent has a description of its responsibilities and signals, and the routing context says to route on what the policyholder wants done, not on the words used.
- **Confidence threshold 0.7:** below it, or when the answer is not an agent, Claude classifies with the same descriptions. If TypeSafe is unavailable (no `TYPESAFE_API_KEY`, network error), the request also falls back to Claude instead of failing. Administration is the last default.
- `pick_agent()` is the only place that calls the router, and accepts a sync or async router returning a name or a decision object.
- If an agent classifies the request as belonging to the other agent (`out_of_scope_claims` / `out_of_scope_administration`), the harness hands it over once, before any effect, and logs the handover and the new routing.
- The returned category is checked against the vocabulary of the handling agent; an invalid one goes back to the model with feedback.
- All requests follow one common path: route, gather facts, check rules, ask or propose, execute, record, answer.

**Files:** `app/runtime.py`, `app/router.py`

**Still open:** pass whether an invoice, prescription or claim ID is attached to the classifier, not only the text. Record in the evaluation log which classifier decided and with what confidence (today it is in the process log only).

## 2. Minimum information, asked once

> Use the minimum-information matrix to collect genuinely missing information once.

**Starter kit:** prompt instructions only. Nothing stopped a second question, an incomplete question, or a question about information the tools already provide.

**What we did**
- **One round, enforced:** a second question is turned into `insufficient_information`; an `unclear` request closes without asking.
- **Matrix in code** (`app/minimum_info.py`): a parser reads the eight invoice items (provider ID, invoice number, invoice date, service date, patient, service, EUR amount, paid status) without a model.
- A prescription must name the same patient as the invoice and a matching service; non-empty text is not enough.
- **The question is generated in code** and lists every gap at once, nothing more.
- The agent cannot ask when nothing is missing, cannot ask for documents for an excluded service (dental, T14), and cannot close as insufficient before asking.
- `create_claim` is blocked while information is missing or when its values differ from the invoice text.
- Contact values must appear in the request or the reply: in T04 the agent cannot invent the postal code.
- A reply can add facts but never replace the original ones; a conflict is recorded in the log.

**Files:** `app/minimum_info.py`, `app/workflow.py`, `app/rules.py`, `tests/test_minimum_info.py`

**Still open:** code checks for coverage changes (target value given) and claim enquiries (record identifiable). A model fallback for invoices whose labels the parser does not recognise.

## 3. Workflow state and stop/resume

> Implement workflow state, correlation and stop/resume for human replies. Keep the same domain run and original facts; never restart a reply as a new request.

**Starter kit:** the runtime handled the first message only. The command line rejected every reply event.

**What we did**
- `app/workflow.py` saves one file per ticket with the run ID, log path, agent, category, facts, conversation, proposal, applied effects and format errors.
- `python -m app run` now accepts `clarification_reply` and `insurer_decision`: it finds the ticket's workflow, reuses the **same run ID** and reloads the existing log.
- A reply is admitted only if the ticket is waiting for that kind of reply; a second copy of the same reply is refused.
- Insurer decisions are handled without the model: approve runs the saved proposal, reject closes the request.

**Files:** `app/workflow.py`, `app/runtime.py`, `app/__main__.py`

**Still open:** re-running a ticket replaces its workflow, so a reply applies to the latest run. An explicit table of allowed state transitions would make the rules visible.

## 4. Admissibility, authority and proposal integrity

> Enforce admissibility, active-policy and date checks, authority and proposal integrity.

**Starter kit:** business rules lived in the prompts. The tools validate formats but do not decide what is allowed.

**What we did**
- **Write gate:** every write tool call passes through `rules.check_tool_call()` before it reaches the domain.
- Only the ticket's own policy can change; the policy must be active; excluded services, uninsured patients and service dates outside cover (both ends included) are refused.
- Coverage decreases are refused; a final annual limit above 5000 or deductible above 200 needs the insurer; termination always does; payments above EUR 1500 do.
- After approval only the exact saved proposal can run: same tool, same arguments.
- A write that already succeeded is never repeated; the earlier result is returned instead.
- Proposals are checked for shape and arguments, and refused when no authorization is actually needed.

**Files:** `app/rules.py`, `app/runtime.py`

**Still open:** rule identifiers in every refusal reason, and a hash of the saved proposal checked at approval.

## 5. Calculation and duplicate prevention

> Implement deterministic Claims calculation and duplicate prevention.

**Starter kit:** the formula and duplicate key were described in the prompt; the model did the arithmetic.

**What we did**
- `calculate_reimbursement()`: max(0, expense − deductible) × 80 %, rounded half-up to cents with `Decimal`, capped by the remaining limit, plus the authorization flag (strictly above EUR 1500).
- `find_duplicate()`: provider ID + invoice number + invoice-date year, against paid or pending records. Pending includes `payment_failed`, because such a record can still be paid; a second record could lead to a double payment.
- Both are offered to the Claims agent as local tools, so it never calculates by itself.
- `pay_claim` is refused unless the amount equals the calculated one, is never sent for 0.00, needs the exact approved proposal above EUR 1500, and only applies to a record created in the same request.
- `tests/test_calculation.py` (34 tests, no model):
  - the policy examples: 180 → 104.00, 1925 → 1500.00 (autonomous), 2000 → 1560.00 (insurer), 2000 with 900 remaining → 900.00 (autonomous);
  - deductible floor, rounding to cents, strict threshold (1500.02 needs the insurer), current deductible after a coverage change, no remaining limit;
  - invariants over about 13,000 amounts: never negative, never above the remaining limit or 80 % of the expense, always two decimals;
  - each part of the duplicate key, and which record statuses block;
  - the gates: duplicate registration refused (T13 → CL-H001), wrong amount, zero payment, approval above the threshold, capped payment.

**Files:** `app/rules.py`, `app/runtime.py`, `tests/test_calculation.py`

**Still open:** nothing for this task.

## 6. Reimbursement-record lifecycle

> When to register a record, how to update outcomes, when a workflow ends without a new record.

| Situation | Record | Tool sequence | Outcome |
|---|---|---|---|
| Excluded, inactive, not insured, outside cover, duplicate, information missing | None | — | `closed_without_execution` |
| Admissible, final ≤ EUR 1500 | Created | `create_claim` → `pay_claim` | `paid` |
| Admissible, final > EUR 1500 | Created, pending | `create_claim` → `record_claim_outcome(pending_authorization)` | `awaiting_authorization` |
| … insurer approves | Paid | `pay_claim` with the saved amount | `paid` |
| … insurer rejects | Rejected | `record_claim_outcome(rejected)` | `insurer_rejected` |
| Final reimbursement 0.00 | Rejected | `create_claim` → `record_claim_outcome(rejected)` | `zero_reimbursement` |
| Payment attempted and failed | Payment failed | `pay_claim` fails → `record_claim_outcome(payment_failed)` | `technical_failure` |

```mermaid
stateDiagram-v2
    [*] --> registered: create_claim (only when admissible, complete, not a duplicate)
    registered --> paid: pay_claim (final ≤ EUR 1500)
    registered --> pending_authorization: final > EUR 1500
    pending_authorization --> paid: insurer approves, pay_claim with the saved amount
    pending_authorization --> rejected: insurer rejects
    registered --> rejected: final is 0.00
    registered --> payment_failed: pay_claim fails
    pending_authorization --> payment_failed: approved pay_claim fails
    registered --> not_covered: closed as not_covered
    registered --> incomplete: closed as insufficient_information
    registered --> invalid: closed for any other reason
```

**Design decisions**
- **No record before the request is admissible.** Exclusions, inactive policies, uninsured patients, dates outside cover, duplicates and missing information close without a record, so history only holds real reimbursement attempts.
- **The record is created before asking the insurer.** The proposal then names an existing `claim_id`, and the pending record blocks a duplicate registration while the insurer decides.
- **No record stays open when a request ends.** When the request closes, the harness stores the actual outcome on any record it created that is still `registered` or `pending_authorization`. The model never has to remember it. Zero reimbursement is recorded as `rejected`.
- **A failed payment is recorded as `payment_failed`.** Such a record can still be paid later, so it also counts as pending for duplicates.
- **Handover keeps the record open.** After a technical failure or handover without a payment attempt, the record stays `registered` for the colleague who takes over, and keeps blocking a duplicate.

**What we did**
- `rules.closing_record_outcome()`: one pure function maps the request outcome to the record outcome (table above).
- `Session.close_records()` applies it when every request ends, through the same write gate as any other call.
- Real `pay_claim` failures are tracked per record, so a failed approved payment ends as `payment_failed` instead of staying `pending_authorization`.
- The Claims prompt no longer asks the model to record outcomes itself.
- `tests/test_claim_lifecycle.py` (20 tests, no model): the mapping, and complete paths on a real domain store with a fake client: zero reimbursement, orphan record, pending while waiting, approve → paid with the remaining limit updated, reject → rejected without payment, failed approved payment → `payment_failed`, handover, valid log.

**Files:** `app/rules.py`, `app/runtime.py`, `agents/claims/prompt.md`, `tests/test_claim_lifecycle.py`

**Still open:** nothing for this task.

## 7. Skills per agent

> Write and use at least one meaningful skill per agent.

**Starter kit:** a loader in `app/skills.py` that nothing called, and empty skill folders.

**What we did**
- **Six skills, one per supported operation:**

  | Agent | Skill | What it adds beyond the policy text |
  |---|---|---|
  | Claims | `reimbursement-request` | Service-to-category table, the order of checks, exact create_claim values, how to explain the amount |
  | Claims | `claim-enquiry` | How to find the record without an ID, what to say for each record status |
  | Administration | `policy-enquiry` | Which policy field answers which question; "how do I terminate?" stays an enquiry |
  | Administration | `contact-update` | Exact value formats, ask only for the missing address parts |
  | Administration | `coverage-change` | Threshold table for the final values, one proposal for a combined change |
  | Administration | `policy-termination` | Always a proposal, never a direct call; already terminated means no_change |

- **Each skill says what the harness will refuse,** so the agent does not try a call that will be blocked. That saves turns.
- **Loaded by category:** `select_skills(agent, category)` reads each skill's frontmatter and returns only the skill for the request's category. Before the category is known, or when none covers it (a request handed over from the other agent), the agent gets all of its skills. Once the router sets the category, only that skill is sent: the Administration prompt for a coverage change drops from about 12,600 to 8,100 characters.
- **Procedures moved out of the prompts:** the prompts keep the general rules; each operation's procedure lives only in its skill.
- `tests/test_skills.py` (9 tests): valid frontmatter, required sections, every supported operation has a skill, selection by category, the template is not loaded, the prompt carries the skill without its frontmatter.

**Files:** `skills/claims/*.md`, `skills/administration/*.md`, `skills/README.md`, `app/skills.py`, `app/runtime.py`, `agents/*/prompt.md`, `tests/test_skills.py`

**Still open:** run the training tickets with the model to compare turns and outcomes with the previous prompts. The router team sets `wf["category"]` so that only one skill is loaded from the first turn.

## 8. Logger integration

> Connect your workflow, classification and human events to the supplied logger.

**Starter kit:** the logger existed and recorded the first message, tool calls and the final answer.

**What we did**
- Routing, every status change with its category, clarification and authorization requests, and each resumed reply are logged; a domain snapshot is saved after every event.
- Refused tool calls are logged with `harness_blocked`, refused repeats as errors, and a format-error handover includes a report of every attempt.
- The clarification request records exactly which items were missing; a reply that contradicts the original facts is noted in the status reason.
- A test checks that every event kind the runtime writes exists in the log schema, after an unknown kind made a T09 run fail.
- **Process log:** besides the evaluation JSON, each run writes a readable `.log` next to it (and to the console): routing, each agent turn and its tools, blocked calls, handovers, insurer decisions and the final outcome. A resumed ticket appends to the same file.

**Files:** `app/runtime.py`, `app/__main__.py`, `app/router.py`, `tests/test_minimum_info.py`

**Still open:** the router's decision and confidence in the evaluation JSON, not only in the process log.

## 9. Actual results and working enquiries

> Communicate actual results, not intended effects, and keep the enquiries working.

**Starter kit:** the model's own answer was logged as the result, whatever the tools had actually done.

**What we did**
- An answer saying `paid`, `updated` or `terminated` is refused unless a successful tool result in this request backs it.
- The agent state is derived from the outcome, so the two can no longer contradict each other.
- After an insurer decision the customer message is built from the tool data: amount, payment ID, values before and after.
- A rejected answer now tells the model what was wrong. Before this fix T04 failed after eleven identical rejections caused by a copied `$schema` key.

**Files:** `app/workflow.py`, `app/runtime.py`

- All 15 training tickets ran with the expected outcome (see [Results](#results-on-the-training-tickets)). Enquiries (T01, T02) answer from tool data and make no writes.
- Each domain run starts from `data/initial.json`, so tickets stay independent; effects are visible in the run's `domain_snapshot`, and a reply keeps the effects of its own run.

**Still open:** live runs of insurer approve and reject on T06, T07 and T11 (covered today by automated tests). Templates for messages on paths without an insurer decision.

---

**Tests:** 139 of 141 pass, including 25 minimum-information, 34 calculation, 20 record-lifecycle and 9 skill tests that need no model. The two failures come from the starter kit: one test still expects replies to be rejected, the other compares a path with Windows separators.

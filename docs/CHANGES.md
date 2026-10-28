# What we changed in the harness

Team 1 · Eurapco hackathon · 28 Oct 2026

For each task in the brief: what the starter kit gave us, what we built on top of it, and what is still open.
Status reflects the code on the branches below, not a scored evaluation run.

| Branch | Content |
|---|---|
| `main` | Core harness: workflow, rules, write gate |
| `fix/response-schema` | Agent answers no longer rejected for a copied `$schema` key |
| `feature/minimum-information` | Minimum-information matrix in code (includes the fix above) |

## Status by task

| # | Task | Status | Main change |
|---|---|---|---|
| 1 | [Router and classification](#1-router-and-classification) | 🟡 Partial | Single routing entry point and agent-to-agent handover; decision model not wired yet |
| 2 | [Minimum information, asked once](#2-minimum-information-asked-once) | ✅ Done | Matrix, invoice parser and generated question in code |
| 3 | [Workflow state and stop/resume](#3-workflow-state-and-stopresume) | ✅ Done | Per-ticket workflow file; replies resume the same run and log |
| 4 | [Admissibility, authority, proposal integrity](#4-admissibility-authority-and-proposal-integrity) | ✅ Done | Every write goes through a rule gate; approval runs the exact saved proposal |
| 5 | [Calculation and duplicate prevention](#5-calculation-and-duplicate-prevention) | 🟡 Partial | Deterministic formula and duplicate key in code; unit tests missing |
| 6 | [Reimbursement-record lifecycle](#6-reimbursement-record-lifecycle) | ✅ Done | Defined when a record is created, paid, pending or rejected |
| 7 | [Skills per agent](#7-skills-per-agent) | ❌ Not started | Loader wired and template written; no skills yet |
| 8 | [Logger integration](#8-logger-integration) | ✅ Done | Routing, status, human rounds, resumes, blocks and gaps all logged |
| 9 | [Actual results, working enquiries](#9-actual-results-and-working-enquiries) | 🟡 Partial | Claimed effects must be backed by tool results; full ticket run pending |

---

## 1. Router and classification

> Improve the router and classification, and organize your harness around the common scope.

**Starter kit:** one regular expression. Any of "claim", "invoice", "reimburse" sends the request to Claims, everything else to Administration. The agent's model then picks the category on its own.

**What we did**
- `pick_agent()` is now the only place that calls the router. It accepts a plain agent name or a full decision object, so the decision model (Jev) can be plugged in without touching the rest of the runtime.
- If an agent classifies the request as belonging to the other agent (`out_of_scope_claims` / `out_of_scope_administration`), the harness hands it over once, before any effect, and logs the handover and the new routing.
- The returned category is checked against the vocabulary of the handling agent; an invalid one goes back to the model with feedback.
- All requests follow one common path: route, gather facts, check rules, ask or propose, execute, record, answer.

**Files:** `app/runtime.py`, `app/router.py`

**Still open:** replace the regex with Jev. Send the context (request text plus whether an invoice, prescription or claim ID is attached), the routing question and the agent descriptions; validate the answer in code, fall back to simple rules if Jev fails, and log who decided.

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
- `find_duplicate()`: provider ID + invoice number + invoice-date year, against paid or pending records.
- Both are offered to the Claims agent as local tools, so it never calculates by itself.
- `pay_claim` is refused unless the amount equals the calculated one.

**Files:** `app/rules.py`, `app/runtime.py`

**Still open:** unit tests with the policy examples: 180 → 104.00, 1925 → 1500.00, 2000 → 1560.00, and 2000 with 900 remaining → 900.00.

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

**Files:** `agents/claims/prompt.md`, `app/runtime.py`, `app/rules.py`

**Still open:** record `payment_failed` on the claim when an approved payment fails, and write the lifecycle up in `docs/design.md`.

## 7. Skills per agent

> Write and use at least one meaningful skill per agent.

**Starter kit:** a loader in `app/skills.py` that nothing called, and empty skill folders.

**What we did**
- The loader is wired in: every skill in `skills/<agent>/` is appended to that agent's system prompt.
- `skills/TEMPLATE.md` defines the format: when to use, what to read first, steps, when to ask the policyholder, when to ask the insurer, when to close.

**Files:** `app/runtime.py`, `skills/TEMPLATE.md`

**Still open:** write the skills (for example `reimbursement-request`, `claim-enquiry`, `coverage-change`, `contact-update`), move the procedures out of the prompts, and load only the skill for the routed category.

## 8. Logger integration

> Connect your workflow, classification and human events to the supplied logger.

**Starter kit:** the logger existed and recorded the first message, tool calls and the final answer.

**What we did**
- Routing, every status change with its category, clarification and authorization requests, and each resumed reply are logged; a domain snapshot is saved after every event.
- Refused tool calls are logged with `harness_blocked`, refused repeats as errors, and a format-error handover includes a report of every attempt.
- The clarification request records exactly which items were missing; a reply that contradicts the original facts is noted in the status reason.
- A test checks that every event kind the runtime writes exists in the log schema, after an unknown kind made a T09 run fail.

**Files:** `app/runtime.py`, `app/__main__.py`, `tests/test_minimum_info.py`

**Still open:** log the router's decision (who decided and why) once Jev is connected.

## 9. Actual results and working enquiries

> Communicate actual results, not intended effects, and keep the enquiries working.

**Starter kit:** the model's own answer was logged as the result, whatever the tools had actually done.

**What we did**
- An answer saying `paid`, `updated` or `terminated` is refused unless a successful tool result in this request backs it.
- The agent state is derived from the outcome, so the two can no longer contradict each other.
- After an insurer decision the customer message is built from the tool data: amount, payment ID, values before and after.
- A rejected answer now tells the model what was wrong. Before this fix T04 failed after eleven identical rejections caused by a copied `$schema` key.

**Files:** `app/workflow.py`, `app/runtime.py`

**Still open:** run T01–T15 with their replies and compare each message with the domain snapshot. Use templates for messages on paths without an insurer decision.

---

**Tests:** 75 of 77 pass, including 25 new minimum-information tests that need no model. The two failures come from the starter kit: one test still expects replies to be rejected, the other compares a path with Windows separators.

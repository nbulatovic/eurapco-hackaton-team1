# Domain skills

One Markdown procedure per supported operation, loaded into the handling agent's system prompt.

| Agent | Skill | Category |
|---|---|---|
| claims | [reimbursement-request](claims/reimbursement-request.md) | reimbursement_request |
| claims | [claim-enquiry](claims/claim-enquiry.md) | claim_enquiry |
| administration | [policy-enquiry](administration/policy-enquiry.md) | policy_enquiry |
| administration | [contact-update](administration/contact-update.md) | contact_update |
| administration | [coverage-change](administration/coverage-change.md) | coverage_change |
| administration | [policy-termination](administration/policy-termination.md) | policy_termination |

## How they are loaded

`app/skills.py` reads each skill's frontmatter. `select_skills(agent, category)` returns only the
skills whose `categories` include the request's category. While the category is still unknown
(before the router sets it), or when no skill covers it, the agent gets all of its skills.
The runtime appends them to the system prompt without the frontmatter.

## Adding a skill

Copy [TEMPLATE.md](TEMPLATE.md) to `skills/<agent>/<name>.md`; files at this root are not loaded.
`tests/test_skills.py` checks the frontmatter, the required sections and that every supported
operation has a skill. Keep each skill consistent with what the harness enforces in code
(`app/rules.py`, `app/minimum_info.py`): say what the harness will refuse, so the agent does not try.

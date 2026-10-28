---
name: policy-enquiry
agent: administration
categories: [policy_enquiry]
description: Answer questions about the policy from the actual policy record, without changing anything.
---

# Policy enquiry

## When to use
- Questions about the policy: what is covered, the deductible, the annual or remaining limit, status, dates, insured people, contacts.
- Questions about how to do something ("How do I terminate my policy?"): these are enquiries, not instructions.
- Not for a request to change something (contact_update, coverage_change, policy_termination).

## Read first
- get_policy for the policy_id in the request.

## Steps
1. Call get_policy.
2. Answer only the questions asked, from the returned fields:
   | Question about | Field |
   |---|---|
   | Is a service covered? | covered_services / excluded_services |
   | Deductible | financial_terms.deductible |
   | Annual limit, used, remaining | financial_terms.annual_limit, paid_consumption, remaining_limit |
   | Status and dates | status, coverage_period |
   | Who is insured | insured_people |
   | Contact details | contacts |
3. For "how do I terminate?": explain that termination needs an explicit instruction from the policyholder and the insurer's authorization. Do not start it.
4. Return completed / answered. An inactive policy can still be described.

## Never
- Call a write tool.
- State a value that get_policy did not return.

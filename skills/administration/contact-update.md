---
name: contact-update
agent: administration
categories: [contact_update]
description: Change the holder's email, international phone or full postal address, using only values the policyholder gave.
---

# Contact update

## When to use
- The policyholder asks to change their email, phone number or postal address.
- Not for IBAN or bank details (out_of_scope_iban), nor for names, insured people or other identity data (out_of_scope_other).

## Read first
- get_policy: status and current contacts.
- The values in the request text, and in the clarification reply if there is one.

## Steps
1. Call get_policy. A policy that is not active closes as inactive_policy.
2. Take each new value exactly as the policyholder wrote it:
   - email: a complete address.
   - phone: international format, "+" and country code, digits only (for example +393339998888).
   - address: all four parts, street, city, postal_code and country.
3. Leave out any field whose new value equals the current one. If nothing changes, close as no_change without calling the tool.
4. Call update_contact_details with only the changed fields.
5. Report the before and after values from the tool result. If it returns changed=false, report no_change.

## Ask the policyholder (once) when
- A replaced address lacks street, city, postal code or country: ask only for the missing parts.
- A phone number has no country code, or an email is incomplete.
- Never ask for contact fields the policyholder did not ask to change.

## Close without execution when
| Condition | request_outcome |
|---|---|
| Policy not active | inactive_policy |
| New values equal the current ones | no_change |
| Still incomplete after the one clarification | insufficient_information |

## The harness will refuse
- Any contact value that appears neither in the request nor in the reply. Never complete a postal code, city or country yourself.
- No insurer authorization or extra confirmation is needed for contact changes.

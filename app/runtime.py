"""Harness around the agent loop: routing, guarded tool calls, and stop/resume for human replies."""

import inspect
import json
import os

from anthropic import AsyncAnthropic
from jsonschema import Draft202012Validator, ValidationError

from agents.administration.agent import system_prompt as admin_prompt
from agents.claims.agent import system_prompt as claims_prompt
from app import rules, workflow
from app.contracts import document, validator
from app.domain import DomainStore, error
from app.mcp_client import DomainClient
from app.router import route
from app.skills import load_skills

MAX_TURNS = 12
MAX_CORRECTIONS = 3
WRONG_AGENT = {"administration": "out_of_scope_claims", "claims": "out_of_scope_administration"}
OTHER_AGENT = {"administration": "claims", "claims": "administration"}

MONEY = {"type": "string", "pattern": r"^(0|[1-9][0-9]*)\.[0-9]{2}$"}
LOCAL_TOOLS = {
    "calculate_reimbursement": {
        "description": "Deterministic reimbursement for an invoice expense on this policy, using its current deductible and remaining limit.",
        "input_schema": {"type": "object", "properties": {"expense": MONEY}, "required": ["expense"], "additionalProperties": False},
    },
    "check_duplicate": {
        "description": "Find an existing paid or pending record with the same provider ID, invoice number and invoice-date year.",
        "input_schema": {"type": "object", "properties": {
            "provider_id": {"type": "string", "minLength": 1}, "invoice_number": {"type": "string", "minLength": 1},
            "invoice_date": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}},
            "required": ["provider_id", "invoice_number", "invoice_date"], "additionalProperties": False},
    },
}


async def pick_agent(request):
    """The only call site of route(). If its signature or return type changes, adapt it here."""
    choice = route(request["text"])
    if inspect.isawaitable(choice):
        choice = await choice
    return choice if isinstance(choice, str) else choice["agent"]


def system_prompt(agent):
    prompt = admin_prompt() if agent == "administration" else claims_prompt()
    for name, text in load_skills(agent):
        prompt += f"\n\n# Skill: {name}\n{text}"
    prompt += "\nReturn ONLY one JSON object matching this response schema when done or waiting:\n"
    return prompt + json.dumps(document("agent-response.schema.json"))


def closing(wf, outcome, reason, message):
    return {"request_category": wf["category"], "agent_state": workflow.STATE_FOR_OUTCOME[outcome],
            "request_outcome": outcome, "reason": reason, "message": message, "question": None, "proposal": None}


class Session:
    """One request's harness: the workflow, its log and the model provider."""

    def __init__(self, wf, log, provider=None):
        self.wf, self.log, self.provider, self.store = wf, log, provider, DomainStore()
        self.owned = provider is None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        if self.owned and self.provider:
            await self.provider.close()

    def model(self):
        """Created on first use: an insurer decision needs no model or API key."""
        if self.provider is None:
            self.provider = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"], max_retries=0, timeout=45)
        return self.provider

    def state(self):
        return self.store.snapshot(self.wf["run_id"])

    def local_tool(self, name, args):
        if not Draft202012Validator(LOCAL_TOOLS[name]["input_schema"]).is_valid(args):
            return error("INVALID_ARGUMENT", f"Arguments do not match the {name} input schema.", retryable=True)
        state = self.state()
        if name == "calculate_reimbursement":
            policy = state["policies"].get(self.wf["facts"]["policy_id"])
            if policy is None:
                return error("NOT_FOUND", "Policy not found in this run.")
            return {"ok": True, "data": rules.calculate_reimbursement(args["expense"], policy)}
        claim = rules.find_duplicate(state["claims"].values(), args["provider_id"], args["invoice_number"], args["invoice_date"])
        return {"ok": True, "data": {"duplicate": claim is not None, "claim": claim}}

    async def call(self, domain, name, args, approved=None):
        """Every tool call goes through here: local tools, business guards, then the real MCP tool."""
        wf = self.wf
        if name in LOCAL_TOOLS and wf["agent"] == "claims":
            self.log.event("tool_call", name=name, arguments=args, local=True)
            result = self.local_tool(name, args)
            self.log.event("tool_result", name=name, result=result, local=True)
        elif previous := next((e["result"] for e in wf["effects"] if e["tool"] == name and e["arguments"] == args), None):
            self.log.event("error", error_type="RepeatedWriteRefused", name=name, arguments=args)
            return previous  # Never repeat a successful write; report what already happened.
        elif objection := rules.check_tool_call(name, args, self.state(), wf, approved):
            result = error("INVALID_STATE", "HARNESS: " + objection[1])
            self.log.event("tool_call", name=name, arguments={"run_id": wf["run_id"], **args})
            self.log.event("tool_result", name=name, result=result, harness_blocked=True)
        else:
            result = await domain.call(name, args)
            if result["ok"] and name in rules.WRITES:
                wf["effects"].append({"tool": name, "arguments": args, "result": result})
        if not result["ok"] and result["error"]["code"] == "INVALID_ARGUMENT":
            wf["format_errors"].setdefault(name, []).append({"arguments": args, "error": result["error"]})
        elif result["ok"]:
            wf["format_errors"].pop(name, None)
        return result

    def handover(self, name):
        attempts = self.wf["format_errors"][name]
        self.log.event("handover", report={
            "request": self.wf["facts"]["text"], "tool": name, "attempts": attempts,
            "why_stopped": f"{name} still failed format validation after {MAX_CORRECTIONS} correction attempts.",
            "suggestion": "A colleague should complete the call manually from the attempts and errors above."})
        return closing(self.wf, "handover_required", f"{name} failed format validation {len(attempts)} times; handed over.",
                       "We could not complete your request automatically. A colleague will take it over.")

    async def loop(self, domain):
        wf, messages = self.wf, self.wf["messages"]
        tools = domain.model_tools() + ([{"name": n, **t} for n, t in LOCAL_TOOLS.items()] if wf["agent"] == "claims" else [])
        prompt = system_prompt(wf["agent"])
        for _ in range(MAX_TURNS):
            response = await self.model().messages.create(
                model=os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5"), max_tokens=1800,
                system=prompt, messages=messages, tools=tools,
            )
            messages.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in response.content]})
            calls = [b for b in response.content if b.type == "tool_use"]
            if calls:
                results = []
                for call in calls:
                    result = await self.call(domain, call.name, call.input)
                    results.append({"type": "tool_result", "tool_use_id": call.id,
                                    "content": json.dumps(result), "is_error": not result["ok"]})
                messages.append({"role": "user", "content": results})
                failing = [name for name, attempts in wf["format_errors"].items() if len(attempts) > MAX_CORRECTIONS]
                if failing:
                    return self.handover(failing[0])
                continue
            text = "".join(b.text for b in response.content if b.type == "text")
            try:
                final = json.loads(text[text.index("{"):text.rindex("}") + 1])  # Tolerate prose or fences around it.
                validator("agent-response.schema.json").validate(final)
            except (ValueError, ValidationError) as exc:
                self.log.event("error", error_type="InvalidAgentResponse", detail=str(exc)[:300])
                messages.append({"role": "user", "content": "HARNESS: Return ONLY one JSON object matching the response schema."})
                continue
            self.log.event("model_response", response=final)
            if final["request_category"] not in document("vocabulary.json")["request_categories"][wf["agent"]]:
                feedback = f"request_category must be one of the {wf['agent']} categories."
            else:
                final, feedback = workflow.settle(final, wf, self.state())
            if feedback:
                messages.append({"role": "user", "content": "HARNESS: " + feedback})
                continue
            return final
        raise RuntimeError("Turn budget exhausted. Stop and hand over; do not repeat successful writes.")

    async def finish(self, domain, final):
        """Record the observed transition; on suspension save what the human reply will resume."""
        wf = self.wf
        wf["category"] = final["request_category"] or wf["category"]
        if final["request_outcome"] == "information_requested":
            wf["clarification_used"] = True
            self.log.human_requested("clarification", {"question": final["question"]})
        elif final["request_outcome"] == "authorization_requested":
            wf["proposal"] = proposal = final["proposal"]
            if proposal["tool"] == "pay_claim":
                await self.call(domain, "record_claim_outcome", {
                    "policy_id": proposal["arguments"]["policy_id"], "claim_id": proposal["arguments"]["claim_id"],
                    "outcome": "pending_authorization", "reason": "Awaiting insurer authorization: " + proposal.get("reason", "")})
            self.log.human_requested("authorization", proposal)
        wf["state"] = final["agent_state"]
        self.log.status(agent_state=final["agent_state"], request_outcome=final["request_outcome"], reason=final["reason"],
                        message=final["message"], request_category=wf["category"])
        workflow.save(wf)
        return final

    async def drive(self):
        """Run the agent; hand over once to the other agent if this one says the request belongs there."""
        wf = self.wf
        while True:
            async with DomainClient(wf["run_id"], wf["agent"], self.log) as domain:
                final = await self.loop(domain)
                if (final["request_category"] == WRONG_AGENT[wf["agent"]] and not wf["handed_over"]
                        and not wf["effects"] and not wf["clarification_used"]):
                    other = OTHER_AGENT[wf["agent"]]
                    self.log.event("handover", from_agent=wf["agent"], to_agent=other, reason=final["reason"])
                    self.log.route(other)
                    wf.update(agent=other, handed_over=True, messages=wf["messages"][:1])
                    continue
                return await self.finish(domain, final)

    async def decide(self, event):
        """Insurer decision: execute exactly the saved proposal, or nothing. No model involved."""
        wf, proposal = self.wf, self.wf["proposal"]
        tool, args = proposal["tool"], proposal["arguments"]
        async with DomainClient(wf["run_id"], wf["agent"], self.log) as domain:
            if event["decision"] == "reject":
                if tool == "pay_claim":
                    await self.call(domain, "record_claim_outcome", {"policy_id": args["policy_id"], "claim_id": args["claim_id"],
                                                                     "outcome": "rejected", "reason": "Insurer rejected: " + event["reason"]})
                final = closing(wf, "insurer_rejected", "Insurer rejected: " + event["reason"],
                                f"The insurer rejected the proposal: {event['reason']} Nothing was changed or paid.")
                return await self.finish(domain, final)
            self.log.status(agent_state="processing", request_outcome="in_progress",
                            reason="Insurer approved; executing the saved proposal.", request_category=wf["category"])
            result = await self.call(domain, tool, args, approved=proposal)
            return await self.finish(domain, approved_result(wf, tool, result))


def approved_result(wf, tool, result):
    if not result["ok"]:
        return closing(wf, "technical_failure", f"Approved {tool} failed: {result['error']['message']}",
                       "The approved operation could not be completed. Nothing was changed.")
    data = result["data"]
    if tool == "pay_claim":
        return closing(wf, "paid", "Insurer approved; the saved payment proposal was executed.",
                       f"Your reimbursement of EUR {data['amount']} was paid (payment {data['payment_id']}, record {data['claim_id']}).")
    if not data["changed"]:
        return closing(wf, "no_change", f"Approved {tool} found nothing to change.", "Nothing needed to change on your policy.")
    if tool == "terminate_policy":
        return closing(wf, "terminated", "Insurer approved; the policy was terminated.",
                       f"Your policy {data['policy_id']} is now terminated.")
    before, after = data["before"], data["after"]
    return closing(wf, "updated", "Insurer approved; the saved coverage change was applied.",
                   f"Your coverage was updated: annual limit EUR {before['annual_limit']} -> {after['annual_limit']}, "
                   f"deductible EUR {before['deductible']} -> {after['deductible']}.")


async def run_request(request, run_id, log, provider=None):
    validator("events.schema.json").validate(request)
    if request["event_type"] != "request":
        raise ValueError("Replies resume an existing workflow: use resume_request.")
    agent = await pick_agent(request)
    log.route(agent)
    async with Session(workflow.new(request, run_id, log.path, agent), log, provider) as session:
        return await session.drive()


async def resume_request(event, wf, log, provider=None):
    """Continue the SAME run and log with a human reply. The reply is admitted only if the ticket waits for it."""
    validator("events.schema.json").validate(event)
    log.input(event)
    expected = workflow.EXPECTED_STATE[event["event_type"]]
    if wf["state"] != expected:
        raise workflow.ReplyNotAdmitted(f"Ticket {wf['ticket_id']} is {wf['state']}, not {expected}: "
                                        f"{event['event_type']} not admitted.")
    wf["state"] = "processing"
    workflow.save(wf)  # A second copy of this reply is refused from now on.
    async with Session(wf, log, provider) as session:
        if event["event_type"] == "insurer_decision":
            return await session.decide(event)
        wf["facts"] = workflow.merge_reply(wf["facts"], event)
        log.status(agent_state="processing", request_outcome="in_progress",
                   reason="Clarification reply received.", request_category=wf["category"])
        wf["messages"].append({"role": "user", "content": "HARNESS: the policyholder's clarification reply (the only one; "
                               "if information is still missing, close with insufficient_information): " + json.dumps(event)})
        return await session.drive()

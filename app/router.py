import logging
import os

from anthropic import AsyncAnthropic
from typesafe_sdk import AsyncTypeSafeClient, Choice

logger = logging.getLogger(__name__)

ROUTING_CONTEXT = (
    "You are a routing agent for a medical insurance service. Each message comes from a policyholder and "
    "contains exactly one operation. Pick the single agent whose responsibility matches that operation. "
    "Route on what the policyholder wants done, not on the words they use: a coverage question that mentions "
    "a treatment still belongs to Administration, and a reimbursement request that mentions the policy still "
    "belongs to Claims. If the operation is unsupported or unclear, choose the agent whose domain is closest. "
    "That agent will close the request as out of scope or unclear. Do not refuse, and do not ask the "
    "policyholder questions."
)


AGENTS = {
    "claims": (
        "Handles medical reimbursement records (claims). Use this agent when the policyholder asks to be "
        "reimbursed for a medical service they already received, such as a specialist consultation, diagnostic "
        "test, physiotherapy, or a recognised excluded service like dental care, cosmetic treatment or "
        "medicines. It also handles questions about the status or details of an existing claim (for example, "
        "'What is the status of claim CL-H001?'). Out-of-scope claim requests also go here, such as adding new "
        "documents to a claim or reopening one that is already decided. Signals: invoice, receipt, "
        "prescription, treatment date, amount paid, 'reimburse', 'refund', claim reference CL-xxx."
    ),
    "administration": (
        "Manages the insurance contract itself. Use this agent when the policyholder asks about their policy: "
        "what is covered or excluded, the annual limit, the deductible, the coverage period, or who is insured. "
        "It also handles changes to holder contact details (email, phone or postal address), requests to "
        "increase the annual limit or deductible, and requests to terminate the policy. Unsupported contract "
        "requests also go here, such as changing the IBAN or renewing the policy. Signals: policy number, "
        "'covered', 'limit', 'deductible', 'address', 'email', 'phone', 'terminate', 'cancel my policy', "
        "'IBAN', 'renewal'. No invoice is submitted for payment."
    ),
}


CONFIDENCE_THRESHOLD = 0.7
ROUTING_QUESTION = "routing question"
DEFAULT_AGENT = "administration"


async def classify(
    text: str,
    agents: dict[str, str] = AGENTS,
) -> tuple[str, float]:

    try:
        async with AsyncTypeSafeClient() as client:
            result = await client.system_one(
                state=ROUTING_CONTEXT,
                questions={
                    ROUTING_QUESTION: Choice(
                        instructions=f"customer question: {text}",
                        criteria=agents,
                    ),
                },
            )

        answer = result.choices[ROUTING_QUESTION]
        return answer.choice, answer.confidence

    except Exception:
        logger.exception("typesafe classification failed")
        raise


async def classify_with_claude(
    text: str,
    agents: dict[str, str] = AGENTS,
) -> str:
    options = "\n".join(
        f"- {name}: {description}" for name, description in agents.items()
    )

    system = (
        f"{ROUTING_CONTEXT}\n\nAgents:\n{options}\n\nReply with only the agent name."
    )

    async with AsyncAnthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"], max_retries=0, timeout=45
    ) as provider:
        response = await provider.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5"),
            max_tokens=20,
            system=system,
            messages=[{"role": "user", "content": text}],
        )

    return (
        "".join(block.text for block in response.content if block.type == "text")
        .strip()
        .lower()
    )


async def route(
    text: str,
    agents: dict[str, str] = AGENTS,
    threshold: float = CONFIDENCE_THRESHOLD,
) -> str:
    try:
        agent, confidence = await classify(text, agents)
    except Exception:
        # Already logged in classify(); an unavailable classifier must not fail the request.
        agent, confidence = None, 0.0
    else:
        logger.info("route: typesafe -> %s (confidence %.2f, threshold %.2f)", agent, confidence, threshold)

    if agent in agents and confidence >= threshold:
        logger.info("route: accepted typesafe choice %s", agent)
        return agent

    logger.info("route: typesafe choice rejected, falling back to claude")
    agent = await classify_with_claude(text, agents)

    if agent in agents:
        logger.info("route: claude -> %s", agent)
        return agent

    logger.warning("route: claude answered %r (not an agent), defaulting to %s", agent, DEFAULT_AGENT)
    return DEFAULT_AGENT

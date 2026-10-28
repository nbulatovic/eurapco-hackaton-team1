"""Run tickets and replies through the harness, or verify supplied infrastructure."""

import argparse
import asyncio
import json
import os
import sys

from dotenv import load_dotenv

from app import workflow
from app.contracts import ROOT, validator
from app.domain import DomainStore
from app.evaluation import EvaluationLog, EvaluationLogError
from app.mcp_client import DomainClient
from app.workflow import ReplyNotAdmitted


class CliError(Exception):
    """A condition the participant can fix; the message is written by this kit and safe to print."""


def root_causes(exc):
    """Leaf exceptions. The MCP client wraps failures raised inside it in nested ExceptionGroups."""
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for inner in exc.exceptions for leaf in root_causes(inner)]
    return [exc]


def cause_names(exc):
    return " + ".join(sorted({type(cause).__name__ for cause in root_causes(exc)}))


async def check():
    store = DomainStore()
    run_id = store.start_run()
    async with DomainClient(run_id, "administration") as domain:
        before = await domain.call("get_policy", {"policy_id": "POL-001"})
        changed = await domain.call("update_contact_details", {"policy_id": "POL-001", "changes": {"phone": "+393339998888"}})
        if not before["ok"] or not changed["ok"] or not changed["data"]["changed"]:
            raise RuntimeError("MCP read/write simulation failed.")
    print("OK: nine tools discovered; isolated MCP read and simulated write completed.")
    print("This check uses no model and does not certify a completed challenge solution.")


async def run(path):
    """An initial request starts a new run; a reply resumes the waiting run of its ticket."""
    from app import runtime
    event = json.loads(path.read_text(encoding="utf-8"))
    validator("events.schema.json").validate(event)
    store = DomainStore()
    if event["event_type"] == "request":
        require_api_key()
        run_id = store.start_run()
        log = EvaluationLog(event, run_id, os.environ.get("TEAM_ID", "TEAM-1"))
        work = runtime.run_request(event, run_id, log)
    else:
        wf = workflow.load(event["ticket_id"])
        if wf is None:
            raise CliError(f"Reply events are not admitted: no workflow for ticket {event['ticket_id']}. Run its initial request first.")
        if event["event_type"] == "clarification_reply":
            require_api_key()
        run_id, log = wf["run_id"], EvaluationLog.load(wf["log_path"])
        work = runtime.resume_request(event, wf, log)
    try:
        final = await work
        print(json.dumps(final, indent=2))
    except Exception as exc:
        # Record exception types (the real causes, not the wrapper), never raw provider bodies/credentials.
        log.event("error", error_type=cause_names(exc))
        if not any(isinstance(cause, (EvaluationLogError, ReplyNotAdmitted)) for cause in root_causes(exc)):
            log.status(agent_state="failed", request_outcome="technical_failure", reason=cause_names(exc))
        raise
    finally:
        log.snapshot(store.snapshot(run_id))
        print(f"Evaluation JSON: {log.path}")
        print(f"Domain run: {run_id}")


def require_api_key():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise CliError("ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key.")


def main():
    from pathlib import Path
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="Check MCP discovery/read/write without an API key.")
    command = sub.add_parser("run", help="Run an initial ticket (new run) or a reply event (resumes its ticket).")
    command.add_argument("ticket", type=Path)
    command = sub.add_parser("validate-log", help="Validate a cumulative evaluation JSON file.")
    command.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "check":
            asyncio.run(check())
        elif args.command == "run":
            asyncio.run(run(args.ticket))
        else:
            record = json.loads(args.path.read_text(encoding="utf-8"))
            errors = list(validator("evaluation.schema.json").iter_errors(record))
            if errors:
                fields = sorted({".".join(map(str, error.absolute_path)) or "record" for error in errors})
                print("ERROR: evaluation log has missing or invalid fields: " + ", ".join(fields), file=sys.stderr)
                return 1
            print("OK: evaluation log matches the common schema.")
    except Exception as exc:
        known = [cause for cause in root_causes(exc) if isinstance(cause, (CliError, EvaluationLogError, ReplyNotAdmitted))]
        if known:
            print(f"ERROR: {known[0]}", file=sys.stderr)
        else:
            print(f"ERROR ({cause_names(exc)}): check configuration, input and the documented baseline limitations. "
                  "The evaluation JSON, when written, records the failure.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

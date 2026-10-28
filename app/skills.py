"""Skill loading: the team's procedures in skills/<agent>/*.md, chosen by request category."""

import re

from app.contracts import ROOT

FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


def load_skills(agent):
    if agent not in ("administration", "claims"):
        raise ValueError("Unknown agent.")
    return [(p.stem, p.read_text(encoding="utf-8")) for p in sorted((ROOT / "skills" / agent).glob("*.md"))]


def parse_skill(text):
    """Split '---' frontmatter (flat 'key: value' lines, lists as [a, b]) from the Markdown body."""
    match = FRONTMATTER.match(text)
    if not match:
        raise ValueError("A skill must start with a '---' frontmatter block.")
    meta = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            value = [item.strip() for item in value[1:-1].split(",") if item.strip()]
        meta[key.strip()] = value
    return meta, text[match.end():].strip()


def select_skills(agent, category=None):
    """(name, description, body) of the skills for this category; all of the agent's skills when the
    category is unknown yet or no skill covers it (e.g. a request handed over from the other agent)."""
    skills = [(name, *parse_skill(text)) for name, text in load_skills(agent)]
    chosen = [skill for skill in skills if category in skill[1]["categories"]] if category else []
    return [(name, meta["description"], body) for name, meta, body in chosen or skills]

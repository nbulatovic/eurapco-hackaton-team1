"""Team skills: valid frontmatter, every supported operation covered, chosen by category, used in the prompt."""

import json

import pytest

from app import runtime
from app.contracts import ROOT
from app.skills import load_skills, parse_skill, select_skills

AGENTS = ("administration", "claims")
CATEGORIES = json.loads((ROOT / "contracts" / "vocabulary.json").read_text(encoding="utf-8"))["request_categories"]
SECTIONS = ("## When to use", "## Read first", "## Steps")


def skills(agent):
    return [(name, *parse_skill(text)) for name, text in load_skills(agent)]


@pytest.mark.parametrize("agent", AGENTS)
def test_frontmatter_is_valid(agent):
    for name, meta, body in skills(agent):
        assert meta["name"] == name, name
        assert meta["agent"] == agent, name
        assert meta["description"], name
        assert meta["categories"] and set(meta["categories"]) <= set(CATEGORIES[agent]), name
        assert all(section in body for section in SECTIONS), name


@pytest.mark.parametrize("agent", AGENTS)
def test_every_supported_operation_has_a_skill(agent):
    supported = {c for c in CATEGORIES[agent] if not c.startswith("out_of_scope") and c != "unclear"}
    covered = {c for _, meta, _ in skills(agent) for c in meta["categories"]}
    assert supported <= covered, supported - covered


def test_template_at_the_root_is_not_loaded():
    assert all(name != "TEMPLATE" for agent in AGENTS for name, _ in load_skills(agent))


def test_only_the_category_skill_is_selected():
    assert [name for name, _, _ in select_skills("claims", "reimbursement_request")] == ["reimbursement-request"]
    assert [name for name, _, _ in select_skills("administration", "coverage_change")] == ["coverage-change"]


def test_all_skills_before_the_category_is_known_or_when_none_covers_it():
    everything = [name for name, _ in load_skills("claims")]
    assert [name for name, _, _ in select_skills("claims")] == everything
    assert [name for name, _, _ in select_skills("claims", "out_of_scope_administration")] == everything


def test_prompt_carries_the_skill_body_without_frontmatter():
    prompt = runtime.system_prompt("claims", "reimbursement_request")
    assert "# Skill: reimbursement-request" in prompt and "calculate_reimbursement" in prompt
    assert "# Skill: claim-enquiry" not in prompt
    assert "categories: [" not in prompt


def test_parse_skill_requires_frontmatter():
    with pytest.raises(ValueError):
        parse_skill("# No frontmatter")

"""Declarative agent skills.

Skills are small, auditable capability descriptions injected into the system
prompt.  They are data, not arbitrary code, so a skill can be enabled per chat
without granting new OS capabilities.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

__all__ = ("Skill", "SKILLS", "available_skills", "skill_names", "render_skills")


@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    summary: str
    guidance: str
    surfaces: tuple[str, ...] = ()


SKILLS: tuple[Skill, ...] = (
    Skill(
        name="research",
        summary="Search the web and summarize sources.",
        guidance=(
            "Prefer web_search for current or niche facts. State uncertainty "
            "and cite the URLs you relied on."
        ),
        surfaces=(".ai", ".ask", ".agent", ".aichat", "dm"),
    ),
    Skill(
        name="diagnostics",
        summary="Inspect traces, code, and runtime status before guessing.",
        guidance=(
            "Use get_traces and code_search before proposing a fix for a "
            "plugin or command. Report the observed cause separately from "
            "the proposed change."
        ),
        surfaces=(".agent", "dm"),
    ),
    Skill(
        name="automation",
        summary="Use the Telegram and Ultroid systems to finish a task.",
        guidance=(
            "When a task changes external state, confirm the target first and "
            "report the result with the concrete identifiers it produced."
        ),
        surfaces=(".agent",),
    ),
)


def available_skills(surface: str | None = None) -> tuple[Skill, ...]:
    if surface is None:
        return SKILLS
    return tuple(skill for skill in SKILLS if surface in skill.surfaces)


def skill_names(surface: str | None = None) -> tuple[str, ...]:
    return tuple(skill.name for skill in available_skills(surface))


def render_skills(surface: str | None = None) -> str:
    skills = available_skills(surface)
    if not skills:
        return ""
    lines = ["Available skills:"]
    lines.extend(
        f"- {skill.name}: {skill.summary} {skill.guidance}" for skill in skills
    )
    return "\n".join(lines)

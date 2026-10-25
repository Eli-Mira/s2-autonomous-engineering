"""Skill router: automatic discovery, classification, selection, loading and pinning.

Reuses existing pinned-brief machinery from qualification_flow.py where available.
"""

import importlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class SkillCategory(Enum):
    BACKEND = "backend"
    FRONTEND = "frontend"
    DATABASE = "database"
    SECURITY = "security"
    TESTING = "testing"
    DEVOPS = "devops"
    DATA = "data"
    PRODUCTIVITY = "productivity"
    COMMUNICATION = "communication"
    CREATIVE = "creative"
    UNKNOWN = "unknown"


@dataclass
class SkillDescriptor:
    name: str
    category: SkillCategory
    description: str
    path: str
    pinned_body_hash: Optional[str] = None
    enabled: bool = True
    conflicts: list = field(default_factory=list)


# Task classification keywords
TASK_PATTERNS = [
    (r"\b(api|backend|server|endpoint|rest|graphql|fastapi|flask|django)\b", SkillCategory.BACKEND),
    (r"\b(ui|frontend|react|component|layout|css|tailwind|design)\b", SkillCategory.FRONTEND),
    (r"\b(database|sql|postgres|prisma|migration|schema|query)\b", SkillCategory.DATABASE),
    (r"\b(security|auth|permission|audit|vulnerability|encrypt)\b", SkillCategory.SECURITY),
    (r"\b(test|testing|unittest|pytest|spec|verification)\b", SkillCategory.TESTING),
    (r"\b(docker|deploy|ci|cd|pipeline|container)\b", SkillCategory.DEVOPS),
    (r"\b(model|ml|ai|inference|training|data)\b", SkillCategory.DATA),
    (r"\b(note|document|write|report|readme)\b", SkillCategory.PRODUCTIVITY),
    (r"\b(discuss|chat|message|email|telegram|whatsapp)\b", SkillCategory.COMMUNICATION),
]


def classify_task(text: str) -> SkillCategory:
    """Classify a task description into a skill category."""
    text_lower = text.lower()
    scores: dict[SkillCategory, int] = {}
    for pattern, category in TASK_PATTERNS:
        matches = len(re.findall(pattern, text_lower))
        if matches:
            scores[category] = scores.get(category, 0) + matches
    if not scores:
        return SkillCategory.UNKNOWN
    return max(scores, key=scores.get)


class SkillRouter:
    """Discovers, selects and pins skills for child execution contexts."""

    def __init__(self, skills_root: Optional[Path] = None):
        self.skills_root = Path(skills_root) if skills_root else Path(
            "C:/Users/emadg/AppData/Local/hermes/skills"
        )
        self._cache: dict[str, SkillDescriptor] = {}
        self._pinned: dict[str, str] = {}  # name -> body hash
        self._discover()

    def _discover(self):
        """Discover installed skills from the skills root."""
        if not self.skills_root.exists():
            return
        for skill_dir in self.skills_root.iterdir():
            if not skill_dir.is_dir():
                continue
            sk = skill_dir / "SKILL.md"
            if not sk.exists():
                continue
            try:
                content = sk.read_text(encoding="utf-8")
            except Exception:
                continue
            name = self._extract_name(content, skill_dir.name)
            desc = self._extract_description(content)
            cat = self._classify_by_name(name)
            self._cache[name] = SkillDescriptor(
                name=name, category=cat, description=desc, path=str(sk)
            )

    @staticmethod
    def _extract_name(content: str, fallback: str) -> str:
        m = re.search(r"^name:\s*(.+)$", content, re.MULTILINE)
        return m.group(1).strip() if m else fallback

    @staticmethod
    def _extract_description(content: str) -> str:
        m = re.search(r"^description:\s*(.+)$", content, re.MULTILINE)
        return m.group(1).strip() if m else ""

    def _classify_by_name(self, name: str) -> SkillCategory:
        name_lower = name.lower()
        mapping = {
            "backend": SkillCategory.BACKEND, "fastapi": SkillCategory.BACKEND,
            "frontend": SkillCategory.FRONTEND, "ui": SkillCategory.FRONTEND,
            "database": SkillCategory.DATABASE, "prisma": SkillCategory.DATABASE,
            "security": SkillCategory.SECURITY, "auth": SkillCategory.SECURITY,
            "testing": SkillCategory.TESTING, "test": SkillCategory.TESTING,
            "devops": SkillCategory.DEVOPS, "deploy": SkillCategory.DEVOPS,
            "data": SkillCategory.DATA, "ml": SkillCategory.DATA,
            "productivity": SkillCategory.PRODUCTIVITY,
            "communication": SkillCategory.COMMUNICATION,
        }
        for key, cat in mapping.items():
            if key in name_lower:
                return cat
        return SkillCategory.UNKNOWN

    def select_for_task(self, task_text: str, description: str = "") -> list[SkillDescriptor]:
        """Select relevant skills for a task description."""
        category = classify_task(task_text + " " + description)
        candidates = [s for s in self._cache.values()
                      if s.category == category and s.enabled]
        return candidates[:5]

    def pin(self, skill_name: str) -> Optional[str]:
        """Pin a skill body and return its hash."""
        desc = self._cache.get(skill_name)
        if not desc:
            return None
        body = Path(desc.path).read_text(encoding="utf-8")
        import hashlib
        h = hashlib.sha256(body.encode("utf-8")).hexdigest()
        self._pinned[skill_name] = h
        return h

    def verify_pinned(self, skill_name: str) -> bool:
        """Verify a pinned skill body matches the current file."""
        pinned = self._pinned.get(skill_name)
        if not pinned:
            return False
        desc = self._cache.get(skill_name)
        if not desc:
            return False
        body = Path(desc.path).read_text(encoding="utf-8")
        import hashlib
        return hashlib.sha256(body.encode("utf-8")).hexdigest() == pinned

    def detect_conflicts(self, skills: list[str]) -> list[str]:
        """Detect conflicting or stale skills."""
        conflicts = []
        for name in skills:
            if name not in self._cache:
                conflicts.append(f"MISSING: {name}")
            elif not self.verify_pinned(name):
                conflicts.append(f"STALE: {name}")
        return conflicts

    def fallback(self, task_text: str) -> list[SkillDescriptor]:
        """Safe fallback: return only core, non-conflicting skills."""
        category = classify_task(task_text)
        return [s for s in self._cache.values()
                if s.category == category and s.enabled and not s.conflicts][:3]

    @property
    def discovered(self) -> dict[str, SkillDescriptor]:
        return dict(self._cache)
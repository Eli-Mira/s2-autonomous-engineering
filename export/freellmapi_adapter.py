"""
FreeLLMAPI Adapter — bridges S2 orchestrator to S3's FreeLLMAPI worker.

Replaces the deterministic-simulated provider with real FreeLLMAPI inference.
Preserves S2's task lifecycle, skill selection, and state machine.
"""

import os
import sys
import json
import asyncio
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Literal
from datetime import datetime, timezone

# Ensure tools directory is on path
TOOLS_DIR = Path(__file__).parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from opencode_worker import run_packet  # S3's real FreeLLMAPI invocation


@dataclass
class ModelReceipt:
    """Standardized receipt from model invocation."""
    task_id: str
    model_id: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    elapsed_ms: int
    success: bool
    error: Optional[str] = None
    raw_response: Optional[str] = None
    code_blocks: list = field(default_factory=list)
    files_written: list = field(default_factory=list)
    test_result: Optional[dict] = None
    repair_attempt: int = 0


class FreeLLMAPIProvider:
    """
    Real FreeLLMAPI provider for S2 orchestrator.
    Wraps S3's run_packet with skill-aware prompting and receipt tracking.
    """
    
    def __init__(self, workspace: Path, freellmapi_only: bool = True):
        self.workspace = Path(workspace)
        self.freellmapi_only = freellmapi_only
        self.receipts: list[ModelReceipt] = []
        
    def _build_prompt(self, task, skill_brief: str, source_context: dict[str, str]) -> str:
        """Build a focused prompt using S2's skill selection and S3's coding conventions."""
        files_list = "\n".join(f"- {k} ({len(v)} chars)" for k, v in source_context.items())
        return f"""You are a senior Python engineer. Complete the task below.

TASK: {task.title}
DESCRIPTION: {task.description}

SKILL GUIDANCE:
{skill_brief}

EXISTING FILES IN WORKSPACE:
{files_list or "(none)"}

REQUIREMENTS:
1. Write clean, tested Python code
2. Follow existing patterns in the workspace
3. Do NOT modify any test files — only implementation files
4. Output ONLY the changed/added files as markdown code blocks with paths
5. No explanations, no markdown outside code blocks

FILES TO PRODUCE:
"""
    
    def invoke(self, task, skill_brief: str, source_context: dict[str, str]) -> ModelReceipt:
        """Execute a real FreeLLMAPI request for the given task."""
        prompt = self._build_prompt(task, skill_brief, source_context)
        
        # Call S3's run_packet with FreeLLMAPI routing
        result = run_packet(
            prompt=prompt,
            workspace=str(self.workspace),
            freellmapi_only=self.freellmapi_only
        )
        
        # Parse result into standardized receipt
        receipt = ModelReceipt(
            task_id=getattr(task, 'id', 'unknown'),
            model_id=result.get('model', 'unknown'),
            provider='freellmapi',
            prompt_tokens=result.get('usage', {}).get('prompt_tokens', 0),
            completion_tokens=result.get('usage', {}).get('completion_tokens', 0),
            total_tokens=result.get('usage', {}).get('total_tokens', 0),
            cost_usd=result.get('cost_usd', 0.0),
            elapsed_ms=result.get('elapsed_ms', 0),
            success=result.get('success', False),
            error=result.get('error'),
            raw_response=result.get('raw_response'),
            code_blocks=result.get('code_blocks', []),
            files_written=result.get('files_written', []),
            test_result=result.get('test_result'),
        )
        
        self.receipts.append(receipt)
        return receipt


def select_free_model(skill_category: str, context_needed: int = 8192) -> tuple[str, dict]:
    """
    Select an eligible free FreeLLMAPI model for the skill category.
    Returns (model_id, route_info).
    """
    routes_path = TOOLS_DIR / "freellmapi_routes.json"
    with open(routes_path) as f:
        routes = json.load(f)
    
    candidates = routes.get(skill_category, routes.get("Coding / Refactoring", []))
    
    # Filter for free models (no paid cost)
    free_candidates = []
    for model_id in candidates:
        # In production, query FreeLLMAPI for current quota; for now use known free tier
        if "flash" in model_id.lower() or "lite" in model_id.lower() or "free" in model_id.lower():
            free_candidates.append(model_id)
    
    if not free_candidates:
        free_candidates = candidates[:3]  # fallback
    
    model_id = free_candidates[0]
    return model_id, {"category": skill_category, "model": model_id, "eligible": True}


def build_skill_brief(skill_router, task) -> str:
    """Extract relevant skill guidance for the task."""
    from skill_router import SkillCategory
    
    # Map specialist to skill category
    specialist_to_category = {
        "backend": SkillCategory.BACKEND,
        "testing": SkillCategory.TESTING,
        "database": SkillCategory.DATABASE,
        "security": SkillCategory.SECURITY,
        "frontend": SkillCategory.FRONTEND,
        "productivity": SkillCategory.PRODUCTIVITY,
        "communication": SkillCategory.COMMUNICATION,
        "devops": SkillCategory.DEVOPS,
        "data": SkillCategory.DATA,
    }
    
    cat = specialist_to_category.get(task.specialist, SkillCategory.BACKEND)
    skills = skill_router.select_skills(cat, task.description)
    
    briefs = []
    for skill in skills[:3]:  # Top 3 skills
        briefs.append(f"## {skill.name}\n{skill.description[:500]}")
    
    return "\n\n".join(briefs) if briefs else "No specific skills selected."


__all__ = ["FreeLLMAPIProvider", "select_free_model", "build_skill_brief", "ModelReceipt"]
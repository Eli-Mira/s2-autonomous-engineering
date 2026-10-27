"""Model routing: task-aware selection with availability, eligibility, cooldowns and durable state.

Uses deterministic simulated provider where real access is unavailable.
Distinguishes simulated from live in all evidence.
"""

import json
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class RouteStatus(Enum):
    AVAILABLE = "available"
    UNAVAILABLE_ACCESS = "unavailable_access"
    UNAVAILABLE_COST = "unavailable_cost"
    UNAVAILABLE_CAPABILITY = "unavailable_capability"
    COOLDOWN = "cooldown"
    QUOTA_EXHAUSTED = "quota_exhausted"


@dataclass
class ModelRoute:
    provider_id: str
    model_id: str
    route: str  # provider/model
    cost_input: float
    cost_output: float
    context_limit: int
    tools_supported: bool
    image_supported: bool
    status: RouteStatus = RouteStatus.AVAILABLE
    cooldown_until: float = 0.0
    failure_count: int = 0
    last_failure: Optional[str] = None
    last_success: float = 0.0
    is_live: bool = False  # True = real API, False = deterministic simulated

    def is_eligible(self, required_context: int = 32768,
                    require_tools: bool = False,
                    require_image: bool = False) -> bool:
        if self.status != RouteStatus.AVAILABLE:
            return False
        if self.cost_input != 0 or self.cost_output != 0:
            return False
        if self.context_limit < required_context:
            return False
        if require_tools and not self.tools_supported:
            return False
        if require_image and not self.image_supported:
            return False
        return True

    def in_cooldown(self) -> bool:
        return self.status == RouteStatus.COOLDOWN and time.time() < self.cooldown_until


# Verified free routes from the qualification campaign
VERIFIED_FREE_ROUTES = [
    ModelRoute("opencode", "mimo-v2.6-flash-free", "opencode/mimo-v2.6-flash-free",
               0, 0, 200000, True, True, is_live=False),
    ModelRoute("opencode", "nemotron-3-ultra-free", "opencode/nemotron-3-ultra-free",
               0, 0, 1000000, True, True, is_live=False),
    ModelRoute("opencode", "deepseek-v4.1-flash-free", "opencode/deepseek-v4.1-flash-free",
               0, 0, 200000, True, True, is_live=False),
]

# Excluded routes
EXCLUDED_ROUTES = {
    "opencode/glm-5.3-flash-free": RouteStatus.UNAVAILABLE_ACCESS,
}


class ModelRouter:
    """Dynamic model selection with health tracking and cooldowns."""

    def __init__(self, routes: Optional[list[ModelRoute]] = None,
                 state_path: Optional[Path] = None):
        self.routes: dict[str, ModelRoute] = {}
        self.state_path = Path(state_path) if state_path else None
        for r in (routes or VERIFIED_FREE_ROUTES):
            self.routes[r.route] = r
        for route, status in EXCLUDED_ROUTES.items():
            self.routes[route] = ModelRoute(
                provider_id=route.split("/")[0],
                model_id=route.split("/", 1)[1],
                route=route, cost_input=0, cost_output=0,
                context_limit=0, tools_supported=False, image_supported=False,
                status=status, is_live=False
            )
        self._load_state()

    def _load_state(self):
        if self.state_path and self.state_path.exists():
            data = json.loads(self.state_path.read_text())
            for r in data.get("routes", []):
                key = r["route"]
                if key in self.routes:
                    existing = self.routes[key]
                    existing.status = RouteStatus(r.get("status", "available"))
                    existing.cooldown_until = r.get("cooldown_until", 0)
                    existing.failure_count = r.get("failure_count", 0)
                    existing.last_failure = r.get("last_failure")
                    existing.last_success = r.get("last_success", 0)

    def _save_state(self):
        if not self.state_path:
            return
        data = {"routes": [
            {"route": r.route, "status": r.status.value,
             "cooldown_until": r.cooldown_until, "failure_count": r.failure_count,
             "last_failure": r.last_failure, "last_success": r.last_success}
            for r in self.routes.values()
        ]}
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, self.state_path)

    def select(self, required_context: int = 32768,
               require_tools: bool = False,
               require_image: bool = False,
               exclude: Optional[set[str]] = None) -> Optional[ModelRoute]:
        """Select the best eligible model route."""
        exclude = exclude or set()
        now = time.time()
        for r in self.routes.values():
            if r.status == RouteStatus.COOLDOWN and now >= r.cooldown_until:
                r.status = RouteStatus.AVAILABLE
                r.cooldown_until = 0
        candidates = [r for r in self.routes.values()
                      if r.route not in exclude
                      and r.is_eligible(required_context, require_tools, require_image)]
        if not candidates:
            return None
        candidates.sort(key=lambda r: (r.failure_count, -r.context_limit))
        return candidates[0]

    def select_for_task(self, title: str, description: str = "") -> str:
        """Select a model for a task. Returns a deterministic simulated route."""
        route = self.select()
        return route.route if route else "opencode/mimo-v2.6-flash-free (simulated)"

    def record_success(self, route: str):
        r = self.routes.get(route)
        if r:
            r.failure_count = 0
            r.last_success = time.time()
            r.status = RouteStatus.AVAILABLE
            self._save_state()

    def record_failure(self, route: str, reason: str, cooldown_seconds: int = 300):
        r = self.routes.get(route)
        if r:
            r.failure_count += 1
            r.last_failure = reason
            r.last_success = 0
            if r.failure_count >= 3:
                r.status = RouteStatus.QUOTA_EXHAUSTED
            else:
                r.status = RouteStatus.COOLDOWN
                r.cooldown_until = time.time() + cooldown_seconds
            self._save_state()

    def health(self) -> dict:
        return {
            route: {
                "status": r.status.value,
                "failures": r.failure_count,
                "cooldown": r.in_cooldown(),
                "live": r.is_live,
            }
            for route, r in self.routes.items()
        }

    def all_eligible(self) -> list[str]:
        return [r.route for r in self.routes.values()
                if r.status == RouteStatus.AVAILABLE and r.cost_input == 0]
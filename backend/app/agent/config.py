"""Loads config/agent.yaml and the system prompt file it points to."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.config import ConfigurationError, Settings

_EFFORT_LEVELS = {"low", "medium", "high", "xhigh", "max"}
_CONNECTORS = {"brightspace", "handshake"}  # live connectors in app.integrations.local


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    min_similarity: float = 0.5
    per_source_limit: int = 2


@dataclass(frozen=True, slots=True)
class GoalConfig:
    name: str
    tools: tuple[str, ...]
    model: str
    effort: str | None
    max_tool_rounds: int
    connectors: tuple[str, ...] = ()  # live connectors the student may select for this goal


@dataclass(frozen=True, slots=True)
class AgentConfig:
    model: str
    effort: str | None
    max_tokens: int
    max_tool_rounds: int
    system_prompt: str
    request_options: dict[str, Any]
    retrieval: RetrievalConfig
    goals: dict[str, GoalConfig] = field(default_factory=dict)

    def goal(self, name: str) -> GoalConfig:
        try:
            return self.goals[name]
        except KeyError as exc:
            raise ConfigurationError(f"Unknown agent goal: {name}") from exc


def _effort(value: Any, where: str) -> str | None:
    if value is None:
        return None
    if value not in _EFFORT_LEVELS:
        raise ConfigurationError(f"{where}: effort must be one of {sorted(_EFFORT_LEVELS)}.")
    return value


def load_agent_config(settings: Settings) -> AgentConfig:
    config_dir: Path = settings.config_dir
    config_path = config_dir / "config" / "agent.yaml"
    try:
        raw = yaml.safe_load(config_path.read_text()) or {}
    except FileNotFoundError as exc:
        raise ConfigurationError(f"Agent config not found: {config_path}") from exc

    prompt_path = config_dir / raw.get("system_prompt_file", "prompts/ai_instructions.md")
    try:
        system_prompt = prompt_path.read_text().strip()
    except FileNotFoundError as exc:
        raise ConfigurationError(f"System prompt file not found: {prompt_path}") from exc
    if not system_prompt:
        raise ConfigurationError(f"System prompt file is empty: {prompt_path}")

    model = raw.get("model")
    if not model:
        raise ConfigurationError("config/agent.yaml must set `model`.")
    effort = _effort(raw.get("effort"), "config/agent.yaml")
    max_tool_rounds = int(raw.get("max_tool_rounds", 8))

    goals: dict[str, GoalConfig] = {}
    for name, goal in (raw.get("goals") or {}).items():
        tools = tuple(goal.get("tools") or ())
        if not tools:
            raise ConfigurationError(f"Goal `{name}` must list at least one tool.")
        connectors = tuple(goal.get("connectors") or ())
        unknown = set(connectors) - _CONNECTORS
        if unknown:
            raise ConfigurationError(
                f"Goal `{name}`: unknown connectors {sorted(unknown)}; use {sorted(_CONNECTORS)}."
            )
        goals[name] = GoalConfig(
            name=name,
            tools=tools,
            model=goal.get("model", model),
            effort=_effort(goal.get("effort", effort), f"goal `{name}`"),
            max_tool_rounds=int(goal.get("max_tool_rounds", max_tool_rounds)),
            connectors=connectors,
        )

    retrieval = raw.get("retrieval") or {}
    return AgentConfig(
        model=model,
        effort=effort,
        max_tokens=int(raw.get("max_tokens", 16000)),
        max_tool_rounds=max_tool_rounds,
        system_prompt=system_prompt,
        request_options=dict(raw.get("request_options") or {}),
        retrieval=RetrievalConfig(
            min_similarity=float(retrieval.get("min_similarity", 0.5)),
            per_source_limit=int(retrieval.get("per_source_limit", 2)),
        ),
        goals=goals,
    )

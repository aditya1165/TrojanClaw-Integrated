"""Runs an agent goal: Claude decides which tools to call.

Each run builds a TrojanClaw MCP server for the signed-in student (so tools only see that
student's data) and connects to it in-process with an MCP client, and the Anthropic SDK's tool runner drives the loop
(Claude asks for tools -> they run through MCP -> results go back -> repeat).

Live connectors (Brightspace, Handshake) are added to the same run: the ones the student
ticked in chat, plus, for requests from this computer, every connector with a saved session
in the signed-in account's folder. Their read-only tools come from the local connector helper
(app.integrations.local) and are named `<provider>__<tool>`. Tool definitions are listed once
per account and cached, so a helper process only starts when Claude actually calls a
connector tool.

Callers can add their own tools and instructions to a run (chat adds the Google Calendar
draft tool this way).
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
import json
import logging
import time
from typing import Any, Callable, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from anthropic import AsyncAnthropic
from anthropic.lib.tools import ToolError, beta_async_tool
from anthropic.lib.tools.mcp import async_mcp_tool
from mcp import Client
from mcp.server.mcpserver import MCPServer

from app.agent.config import AgentConfig
from app.errors import AppError, DependencyUnavailableError
from app.services.sources import activity_for, cited_links, collect_links

_DEFAULT_TIMEZONE = "America/Los_Angeles"
_LOG = logging.getLogger(__name__)
# Connector states (app.integrations.models.IntegrationState) that mean a usable saved session.
_SAVED_STATES = {"saved", "connected"}

# Builds the TrojanClaw MCP server for one student.
McpServerFactory = Callable[[UUID], MCPServer]
# Receives progress while a run streams: {"type": "text" | "reset" | "activity", ...}.
OnEvent = Callable[[dict[str, Any]], None]

# Shown as the source when an answer used a live connector.
_CONNECTOR_SOURCES = {
    "brightspace": {"title": "Brightspace", "url": "https://brightspace.usc.edu", "kind": "connector"},
    "handshake": {"title": "Handshake", "url": "https://usc.joinhandshake.com", "kind": "connector"},
    "piazza": {"title": "Piazza", "url": "https://piazza.com", "kind": "connector"},
}


class ConnectorTools(Protocol):
    """The parts of LocalConnectors the runtime uses (blocking; run in a thread).

    Every method takes the signed-in account's ID: sessions are stored per account.
    """

    def statuses(self, user_id: UUID) -> list[Any]: ...

    def tools(self, user_id: UUID, providers: list[str]) -> list[dict[str, Any]]: ...

    def call(self, user_id: UUID, name: str, arguments: dict[str, Any], providers: list[str]) -> str: ...


@dataclass(slots=True)
class ToolCall:
    tool: str
    arguments: dict[str, Any]
    is_error: bool
    duration_ms: int


@dataclass(slots=True)
class AgentResult:
    text: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str | None = None
    model: str | None = None


class _RecordingClient:
    """Wraps the MCP client to record each tool call and the sources it returned."""

    def __init__(self, client: Client, on_event: OnEvent | None = None):
        self._client = client
        self._on_event = on_event
        self.tool_calls: list[ToolCall] = []
        self.sources: list[dict[str, Any]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None, **kwargs: Any):
        if self._on_event:
            self._on_event({"type": "activity", "text": activity_for(name)})
        started = time.monotonic()
        result = await self._client.call_tool(name=name, arguments=arguments, **kwargs)
        is_error = bool(getattr(result, "is_error", False))
        self.tool_calls.append(
            ToolCall(name, dict(arguments or {}), is_error, int((time.monotonic() - started) * 1000))
        )
        if not is_error:
            for block in getattr(result, "content", None) or []:
                try:
                    payload = json.loads(getattr(block, "text", "") or "")
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    self.sources.extend(
                        s for s in payload.get("sources") or [] if isinstance(s, dict) and s.get("title")
                    )
        return result


def _dedupe(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, Any]] = set()
    unique = []
    for source in sources:
        key = (source.get("title"), source.get("url"))
        if key not in seen:
            seen.add(key)
            unique.append(source)
    return unique


def _connector_tools(
    connectors: ConnectorTools,
    user_id: UUID,
    definitions: list[dict[str, Any]],
    providers: list[str],
    tool_calls: list[ToolCall],
    sources: list[dict[str, Any]],
    links: dict[str, Any] | None = None,
    on_event: OnEvent | None = None,
) -> list[Any]:
    """Wrap connector tool definitions as runnable tools for the tool runner.

    Links found in results go into `links`, so the reply can show the ones it cites."""

    def make(definition: dict[str, Any]) -> Any:
        name = definition["name"]
        provider = name.partition("__")[0]

        async def call(**arguments: Any) -> str:
            if on_event:
                on_event({"type": "activity", "text": activity_for(name)})
            started = time.monotonic()
            try:
                # Blocking subprocess call (up to ~100 s); keep the event loop free.
                result = await asyncio.to_thread(connectors.call, user_id, name, arguments, providers)
            except AppError as exc:
                tool_calls.append(ToolCall(name, arguments, True, int((time.monotonic() - started) * 1000)))
                raise ToolError(exc.message) from None
            tool_calls.append(ToolCall(name, arguments, False, int((time.monotonic() - started) * 1000)))
            if provider in _CONNECTOR_SOURCES:
                sources.append(dict(_CONNECTOR_SOURCES[provider], course=None))
            if links is not None:
                collect_links(result, provider, links)
            return result

        return beta_async_tool(
            call,
            name=name,
            description=definition.get("description") or name,
            input_schema=definition.get("input_schema") or {"type": "object", "properties": {}},
        )

    return [make(definition) for definition in definitions]


def _connectors_line(providers: list[str]) -> str:
    if not providers:
        return "Live connectors available in this chat: none."
    names = ", ".join(provider.title() for provider in providers)
    prefixes = ", ".join(f"`{provider}__`" for provider in providers)
    return f"Live connectors available in this chat: {names} (tools starting with {prefixes})."


def _state(status: Any) -> str:
    state = getattr(status, "state", None)
    return str(getattr(state, "value", state))


def _provider(status: Any) -> str:
    provider = getattr(status, "provider", None)
    return str(getattr(provider, "value", provider))


def _now_line(timezone: str) -> str:
    now = datetime.now(ZoneInfo(timezone))
    # Windows does not support strftime's %-d / %-I padding modifiers.
    display = f"{now:%A, %B} {now.day}, {now.year}, {now.hour % 12 or 12}:{now:%M %p}"
    return (
        f"Current date and time: {display} ({now.isoformat(timespec='minutes')}). "
        f"The student's time zone: {timezone}."
    )


class AgentRuntime:
    def __init__(
        self,
        config: AgentConfig,
        server_for_user: McpServerFactory,
        anthropic_api_key: str | None,
        client: AsyncAnthropic | None = None,
        connectors: ConnectorTools | None = None,
    ):
        self._config = config
        self._server_for_user = server_for_user
        self._api_key = anthropic_api_key
        self._client = client
        self._connectors = connectors
        # (account ID, provider) -> tool definitions.
        self._connector_definitions: dict[tuple[str, str], list[dict[str, Any]]] = {}

    @property
    def config(self) -> AgentConfig:
        return self._config

    def anthropic(self) -> AsyncAnthropic:
        """The shared Anthropic client (chat titles use it too)."""
        return self._anthropic()

    def _anthropic(self) -> AsyncAnthropic:
        if self._client is None:
            if not self._api_key:
                raise DependencyUnavailableError("Anthropic")
            self._client = AsyncAnthropic(api_key=self._api_key)
        return self._client

    async def _definitions(self, user_id: UUID, provider: str) -> list[dict[str, Any]]:
        """The connector's tool definitions, listed through the helper once per account and cached."""
        assert self._connectors is not None
        key = (str(user_id), provider)
        if key not in self._connector_definitions:
            self._connector_definitions[key] = await asyncio.to_thread(
                self._connectors.tools, user_id, [provider]
            )
        return self._connector_definitions[key]

    async def _connector_definitions_for(
        self, user_id: UUID, allowed: Sequence[str], selected: Sequence[str], use_saved: bool
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """Connectors for this run: ticked ones (errors surface) plus saved ones (errors skipped)."""
        chosen = [p for p in dict.fromkeys(selected) if p in allowed]
        if chosen and self._connectors is None:
            raise DependencyUnavailableError("Live connectors")
        saved: list[str] = []
        if use_saved and self._connectors is not None:
            statuses = await asyncio.to_thread(self._connectors.statuses, user_id)
            saved = [
                _provider(s) for s in statuses
                if _state(s) in _SAVED_STATES and _provider(s) in allowed and _provider(s) not in chosen
            ]
        providers, definitions = [], []
        for provider in chosen + saved:
            try:
                definitions += await self._definitions(user_id, provider)
            except AppError:
                if provider in chosen:
                    raise
                _LOG.warning("Skipping saved %s session: its tools could not be listed", provider)
                continue
            providers.append(provider)
        return providers, definitions

    async def run(
        self,
        goal: str,
        messages: list[dict[str, Any]],
        *,
        user_id: UUID,
        connectors: Sequence[str] = (),
        use_saved_connectors: bool = False,
        timezone: str = _DEFAULT_TIMEZONE,
        extra_tools: Sequence[Any] = (),
        extra_instructions: str | None = None,
        include_goal_tools: bool = True,
        on_event: OnEvent | None = None,
        allowed_connectors: Sequence[str] | None = None,
    ) -> AgentResult:
        """Run a goal for one student.

        `extra_tools` (runnable tool-runner tools) and `extra_instructions` are added by the
        caller. With `include_goal_tools=False` Claude gets only `extra_tools` (no MCP or
        connector tools), for a narrow follow-up step.

        With `on_event` the reply streams: {"type": "text"} as it is written, {"type": "reset"}
        when text so far was only a lead-in to tool calls, and {"type": "activity"} per tool call.

        `allowed_connectors` limits connectors further than the goal's list: the sources the
        student turned on in chat. None means no extra limit.
        """
        goal_config = self._config.goal(goal)
        anthropic = self._anthropic()
        providers, definitions = (
            await self._connector_definitions_for(
                user_id,
                [p for p in goal_config.connectors if allowed_connectors is None or p in allowed_connectors],
                connectors, use_saved_connectors,
            )
            if include_goal_tools
            else ([], [])
        )
        # The instructions are fixed text; the date, sources and caller notes go in a second block.
        context = _now_line(timezone) + "\n" + _connectors_line(providers)
        if extra_instructions:
            context += "\n\n" + extra_instructions
        system = [
            {"type": "text", "text": self._config.system_prompt},
            {"type": "text", "text": context},
        ]
        extra: dict[str, Any] = dict(self._config.request_options)
        if goal_config.effort:
            extra["output_config"] = {"effort": goal_config.effort}

        links: dict[str, Any] = {}
        async with Client(self._server_for_user(user_id)) as mcp_client:
            recorder = _RecordingClient(mcp_client, on_event)
            listed = await mcp_client.list_tools()
            tools = [
                async_mcp_tool(tool, recorder)  # type: ignore[arg-type]  # duck-typed call_tool
                for tool in listed.tools
                if include_goal_tools and tool.name in goal_config.tools
            ]
            if providers:
                assert self._connectors is not None
                tools += _connector_tools(
                    self._connectors, user_id, definitions, providers,
                    recorder.tool_calls, recorder.sources, links, on_event,
                )
            tools += list(extra_tools)
            request = dict(
                model=goal_config.model,
                max_tokens=self._config.max_tokens,
                system=system,
                tools=tools,
                messages=messages,
                max_iterations=goal_config.max_tool_rounds,
                **extra,
            )
            if on_event is None:
                final = await anthropic.beta.messages.tool_runner(**request).until_done()
            else:
                final = await _stream(anthropic.beta.messages.tool_runner(**request, stream=True), on_event)

        text = "\n".join(
            block.text for block in final.content if getattr(block, "type", None) == "text"
        ).strip()
        if final.stop_reason == "refusal":
            text = "I can't help with that request."
        elif not text:
            text = (
                "I couldn't finish answering within the allowed number of steps. "
                "Please try a more specific question."
            )
        sources = _dedupe(recorder.sources)
        cited = cited_links(text, links)
        if cited:
            # Specific links the reply cites replace the connector's home page.
            providers = {item.pop("provider") for item in cited}
            sources = cited + [s for s in sources if not (s.get("kind") == "connector" and s.get("title", "").lower() in providers)]
        return AgentResult(
            text=text,
            sources=sources,
            tool_calls=recorder.tool_calls,
            stop_reason=final.stop_reason,
            model=getattr(final, "model", None),
        )


async def _stream(runner: Any, on_event: OnEvent) -> Any:
    """Pass the reply's text on as it is written; return the last message."""
    async for stream in runner:
        wrote = False
        async for event in stream:
            if event.type == "content_block_delta" and getattr(event.delta, "type", None) == "text_delta":
                wrote = True
                on_event({"type": "text", "text": event.delta.text})
        message = await stream.get_final_message()
        if wrote and any(getattr(block, "type", None) == "tool_use" for block in message.content):
            on_event({"type": "reset"})  # that text led into tool calls; the answer comes next
    return await runner.until_done()

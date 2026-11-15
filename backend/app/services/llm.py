"""Language-model provider abstraction."""

from anthropic import Anthropic
from collections.abc import Callable

from app.config import Settings
from app.errors import AppError, DependencyUnavailableError
from app.models.chat import ChatMessage


class LanguageModelService:
    def __init__(self, settings: Settings):
        self._settings = settings

    def respond(self, messages: list[ChatMessage], system_prompt: str) -> str:
        if not self._settings.anthropic_api_key:
            raise DependencyUnavailableError("Anthropic")
        client = Anthropic(api_key=self._settings.anthropic_api_key)
        response = client.messages.create(
            model=self._settings.anthropic_model,
            max_tokens=1024,
            system=system_prompt,
            messages=[
                {"role": message.role.value, "content": message.content}
                for message in messages
            ],
        )
        text_blocks = [
            block.text for block in response.content if getattr(block, "type", None) == "text"
        ]
        if not text_blocks:
            raise DependencyUnavailableError("Anthropic response")
        return "\n".join(text_blocks)

    def respond_with_tools(
        self, messages: list[ChatMessage], system_prompt: str,
        tools: list[dict], call_tool: Callable[[str, dict], str],
    ) -> str:
        if not self._settings.anthropic_api_key:
            raise DependencyUnavailableError("Anthropic")
        conversation = [{"role": message.role.value, "content": message.content} for message in messages]
        permitted = {tool["name"] for tool in tools}
        with Anthropic(api_key=self._settings.anthropic_api_key) as client:
            for _ in range(6):
                response = client.messages.create(
                    model=self._settings.anthropic_model, max_tokens=2048,
                    system=system_prompt, messages=conversation, tools=tools,
                )
                calls = [block for block in response.content if block.type == "tool_use"]
                if not calls:
                    text = "\n".join(block.text for block in response.content if block.type == "text")
                    return text or "I could not find an answer from the connected sources."
                conversation.append({"role": "assistant", "content": [
                    block.model_dump(exclude_none=True) for block in response.content
                ]})
                results = []
                for block in calls[:8]:
                    try:
                        if block.name not in permitted or not isinstance(block.input, dict):
                            raise AppError("This tool is not available.", "tool_forbidden", 403)
                        content = call_tool(block.name, block.input)
                        is_error = False
                    except AppError as exc:
                        content, is_error = exc.message, True
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": content, "is_error": is_error})
                # Every requested tool_use must receive a corresponding result.
                results.extend({"type": "tool_result", "tool_use_id": block.id,
                                "content": "Too many tools requested at once. Narrow the request.",
                                "is_error": True} for block in calls[8:])
                conversation.append({"role": "user", "content": results})
        return "I reached the lookup limit. Please narrow your question to a course, job, or date range."

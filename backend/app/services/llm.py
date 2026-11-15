"""Language-model provider abstraction."""

from anthropic import Anthropic

from app.config import Settings
from app.errors import DependencyUnavailableError
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

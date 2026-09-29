"""Explicit subscription-only model routes; schema errors never trigger fallback."""

from typing import TypeVar

import structlog
from pydantic import BaseModel

from market_digest.ai.client import StructuredAIClient
from market_digest.errors import LunaUnavailableError

T = TypeVar("T", bound=BaseModel)
log = structlog.get_logger(__name__)


class ModelRoute:
    def __init__(self, clients: list[tuple[str, StructuredAIClient]]) -> None:
        if not clients:
            raise ValueError("Model route cannot be empty")
        self.clients = clients
        self.used_models: list[str] = []
        self.failures: list[str] = []

    def reset(self) -> None:
        self.used_models.clear()
        self.failures.clear()

    async def parse(
        self, *, operation: str, system_prompt: str, payload: str, schema: type[T]
    ) -> T:
        for model, client in self.clients:
            try:
                result = await client.parse(
                    operation=operation, system_prompt=system_prompt, payload=payload, schema=schema
                )
                if model not in self.used_models:
                    self.used_models.append(model)
                return result
            except LunaUnavailableError:
                if model not in self.failures:
                    self.failures.append(model)
                log.warning("codex_model_unavailable", model=model, operation=operation)
        raise LunaUnavailableError("All configured Codex models are unavailable")

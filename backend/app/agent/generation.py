"""G09 content generation reusing the G08 model adapter, not a new provider link."""

from typing import Protocol

from app.schemas.agent import ModelGenerateRequest, ModelGenerateResult


class ModelAdapterLike(Protocol):
    async def generate(self, request: ModelGenerateRequest) -> ModelGenerateResult: ...


class ContentGenerator:
    """Thin bridge to ``app.state.model_adapter`` from PR #29."""

    def __init__(self, model_adapter: ModelAdapterLike) -> None:
        self._model_adapter = model_adapter

    async def generate(self, request: ModelGenerateRequest) -> ModelGenerateResult:
        """Return the shared result unchanged; G10 owns AgentResult degradation."""
        return await self._model_adapter.generate(request)

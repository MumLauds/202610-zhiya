"""G09 facade: retrieval + prompt + generation, returning intermediate results only."""

from dataclasses import dataclass
from pathlib import Path

from app.schemas.agent import (
    AgentSource,
    KnowledgeQuery,
    KnowledgeRetrievalResult,
    ModelGenerateRequest,
    ModelGenerateResult,
)

from .generation import ContentGenerator
from .prompts import ContentTask, build_prompt, select_knowledge_items
from .retrieval import KnowledgeRetriever

TASK_TO_KNOWLEDGE_TYPE: dict[str, str] = {
    "RECOMMENDATION_REASON": "RETRIEVAL",
    "CONTEXT_QUESTION": "GUIDANCE",
    "STORY_ANSWER": "RETRIEVAL",
}


@dataclass(frozen=True)
class ContentDraft:
    """Internal G09 intermediate result. This is NOT an AgentResult.

    G10 combines this with safety checks and degradation rules to construct the
    final ``AgentResult``. No status, strategyType or outputText is produced here.
    """

    task: ContentTask
    text: str
    sources: list[AgentSource]
    has_knowledge: bool
    model_error_code: str | None
    request: ModelGenerateRequest


class G09ContentService:
    def __init__(self, retriever: KnowledgeRetriever, generator: ContentGenerator) -> None:
        self._retriever = retriever
        self._generator = generator

    @classmethod
    def from_app_state(cls, app_state: object, *, path: Path | None = None,
                       limit: int = 5) -> "G09ContentService":
        """Build from FastAPI app.state, which exposes model_adapter since PR #29."""
        return cls(
            KnowledgeRetriever(path, limit=limit),
            ContentGenerator(app_state.model_adapter),  # type: ignore[attr-defined]
        )

    def retrieve(self, query: KnowledgeQuery) -> KnowledgeRetrievalResult:
        return self._retriever.retrieve(query)

    def build_request(
        self,
        task: ContentTask,
        *,
        age_band: str | None,
        page_text: str,
        user_input: str,
        topics: list[str],
        query_text: str = "",
        max_items: int = 5,
        ) -> tuple[ModelGenerateRequest, KnowledgeRetrievalResult]:
        """Expose the prompt and retrieval result so G10 can inspect or override them."""
        retrieval = self.retrieve(KnowledgeQuery(
            age_band=age_band,
            topics=topics,
            task_type=TASK_TO_KNOWLEDGE_TYPE[task],
            query_text=query_text,
        ))
        request = build_prompt(
            task,
            age_band=age_band,
            page_text=page_text,
            items=retrieval.items,
            user_input=user_input,
            max_items=max_items,
        )
        return request, retrieval

    async def generate(
        self,
        task: ContentTask,
        *,
        age_band: str | None,
        page_text: str,
        user_input: str,
        topics: list[str],
        query_text: str = "",
        max_items: int = 5,
    ) -> ContentDraft:
        request, retrieval = self.build_request(
            task,
            age_band=age_band,
            page_text=page_text,
            user_input=user_input,
            topics=topics,
            query_text=query_text,
            max_items=max_items,
        )
        selected = select_knowledge_items(retrieval.items, max_items)
        if not selected:
            return ContentDraft(
                task=task,
                text="",
                sources=[],
                has_knowledge=False,
                model_error_code=None,
                request=request,
            )

        result: ModelGenerateResult = await self._generator.generate(request)
        sources = [
            AgentSource(knowledgeItemId=item.knowledge_item_id, sourceTitle=item.source_title)
            for item in selected
        ]
        return ContentDraft(
            task=task,
            text=result.text if result.success else "",
            sources=sources,
            has_knowledge=True,
            model_error_code=result.error_code,
            request=request,
        )

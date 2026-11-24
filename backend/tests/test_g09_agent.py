import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app.agent import G09ContentService, KnowledgeRetriever, build_prompt
from app.schemas.agent import (
    AgentResult,
    KnowledgeQuery,
    ModelGenerateRequest,
    ModelGenerateResult,
)

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "database" / "schema.sql"


def _insert_item(db, *, id, version_no, item_key, topics, task_types, status,
                 age_band="AGE_3_4", content="示例内容", reviewed_at=None):
    db.execute(
        "INSERT INTO knowledge_items "
        "(id, version_no, item_key, title, source_title, source_ref, locator, "
        " age_band, topics_json, task_types_json, content, status, reviewed_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
        (
            id, version_no, item_key, f"标题{id}", "知芽｜知识库知识条目 v2.5",
            f"https://example.invalid/kn#{id}", item_key, age_band,
            json.dumps(topics, ensure_ascii=False),
            json.dumps(task_types, ensure_ascii=False),
            content, status, reviewed_at,
        ),
    )


class G09DatabaseMixin:
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "zhiya.sqlite3"
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.executescript(SCHEMA.read_text(encoding="utf-8"))
            _insert_item(db, id=1, version_no=1, item_key="KN-AGE-001",
                         topics=["年龄发展"], task_types=["GUIDANCE"],
                         status="DRAFT", content="未批准的旧版本。")
            _insert_item(db, id=2, version_no=2, item_key="KN-AGE-001",
                         topics=["年龄发展", "动物"], task_types=["GUIDANCE"],
                         status="APPROVED", reviewed_at=2, content="能说出故事里的主要人物和情节。")
            _insert_item(db, id=3, version_no=1, item_key="KN-STORY-001",
                         topics=["动物", "互助"], task_types=["RETRIEVAL", "GUIDANCE"],
                         status="APPROVED", reviewed_at=3, age_band=None,
                         content="小兔子把雨伞借给了淋雨的小熊。")
            _insert_item(db, id=4, version_no=1, item_key="KN-DRAFT-001",
                         topics=["动物"], task_types=["GUIDANCE"], status="DRAFT",
                         content="尚未审核的知识。")
            _insert_item(db, id=5, version_no=1, item_key="KN-SAFE-001",
                         topics=["安全"], task_types=["SAFETY"], status="APPROVED",
                         reviewed_at=5, age_band=None, content="遇到危险请找爸爸妈妈。")

    def tearDown(self):
        self._tmp.cleanup()


class KnowledgeRetrieverTests(G09DatabaseMixin, unittest.TestCase):
    def test_filters_approved_age_topics_and_latest_version(self):
        query = KnowledgeQuery(
            age_band="AGE_3_4", topics=["动物"], task_type="GUIDANCE",
            query_text="雨伞 小兔子",
        )
        result = KnowledgeRetriever(self.db_path).retrieve(query)
        self.assertTrue(result.has_results)
        self.assertEqual([item.item_key for item in result.items],
                         ["KN-STORY-001", "KN-AGE-001"])
        self.assertEqual(result.items[1].knowledge_item_id, 2)
        self.assertEqual(result.items[0].source_title, "知芽｜知识库知识条目 v2.5")

    def test_all_age_query_only_matches_all_age_items(self):
        query = KnowledgeQuery(
            age_band=None, topics=["安全"], task_type="SAFETY", query_text="",
        )
        result = KnowledgeRetriever(self.db_path).retrieve(query)
        self.assertEqual([item.item_key for item in result.items], ["KN-SAFE-001"])

    def test_topic_match_count_takes_priority_over_keywords(self):
        query = KnowledgeQuery(
            age_band="AGE_3_4", topics=["年龄发展", "动物"], task_type="GUIDANCE",
            query_text="雨伞 小兔子",
        )
        result = KnowledgeRetriever(self.db_path, limit=1).retrieve(query)
        self.assertEqual([item.item_key for item in result.items], ["KN-AGE-001"])

    def test_missing_database_returns_empty_result(self):
        query = KnowledgeQuery(
            age_band="AGE_3_4", topics=["动物"], task_type="GUIDANCE", query_text="",
        )
        result = KnowledgeRetriever(Path(self._tmp.name) / "missing.sqlite3").retrieve(query)
        self.assertFalse(result.has_results)
        self.assertEqual(result.items, [])

    def test_natural_chinese_question_ranks_relevant_item_first(self):
        """A contiguous Chinese question must not collapse to topic/id ordering."""
        class SentenceRetriever(KnowledgeRetriever):
            def _rows(self):
                base = {
                    "title": "标题", "source_title": "来源", "source_ref": "ref",
                    "age_band": None, "topics_json": '["动物"]',
                    "task_types_json": '["GUIDANCE"]', "status": "APPROVED",
                    "version_no": 1,
                }
                return [
                    dict(base, id=2, item_key="KN-IRRELEVANT",
                         content="小猫站在门边抬头看小鸟。"),
                    dict(base, id=1, item_key="KN-UMBRELLA",
                         content="小兔子把雨伞借给了淋雨的小熊。"),
                ]

        query = KnowledgeQuery(
            age_band="AGE_3_4", topics=["动物"], task_type="GUIDANCE",
            query_text="小兔子为什么把雨伞借给小熊",
        )
        result = SentenceRetriever(limit=1).retrieve(query)
        self.assertEqual([item.item_key for item in result.items], ["KN-UMBRELLA"])

    def test_chinese_keywords_select_relevant_item(self):
        """Single characters and full questions must beat the ID tie-breaker."""
        from app.agent.retrieval import _tokens

        # Keyword extraction keeps meaningful single characters, drops stop chars.
        self.assertIn("伞", _tokens("伞"))
        self.assertIn("雨", _tokens("雨"))
        self.assertEqual(_tokens("的了"), [])
        self.assertEqual(_tokens("！？，。"), [])
        self.assertEqual(_tokens(" \t\n"), [])
        self.assertEqual(_tokens("Rabbit umbrella RABBIT"), ["rabbit", "umbrella"])

        # Real database retrieval: same age/topic/version, limit=1 must pick id 1.
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute("DELETE FROM knowledge_items")
            _insert_item(db, id=1, version_no=1, item_key="KN-UMBRELLA",
                         topics=["动物"], task_types=["GUIDANCE"], status="APPROVED",
                         reviewed_at=1, content="小兔子把雨伞借给了淋雨的小熊。")
            _insert_item(db, id=2, version_no=1, item_key="KN-CAT",
                         topics=["动物"], task_types=["GUIDANCE"], status="APPROVED",
                         reviewed_at=2, content="小猫站在门边抬头看小鸟。")
            db.execute("UPDATE knowledge_items SET title = '动物故事'")

        for keyword in ("伞", "雨", "雨伞 小兔子", "小兔子为什么把雨伞借给小熊"):
            with self.subTest(keyword=keyword):
                query = KnowledgeQuery(
                    age_band="AGE_3_4", topics=["动物"], task_type="GUIDANCE",
                    query_text=keyword,
                )
                result = KnowledgeRetriever(self.db_path, limit=1).retrieve(query)
                self.assertEqual([item.knowledge_item_id for item in result.items], [1])

    def test_duplicate_item_key_keeps_latest_approved_version(self):
        class DuplicateRetriever(KnowledgeRetriever):
            def _rows(self):
                base = {
                    "item_key": "KN-X", "title": "标题", "source_title": "来源",
                    "source_ref": "ref", "age_band": None,
                    "topics_json": '["动物"]', "task_types_json": '["GUIDANCE"]',
                    "content": "内容", "status": "APPROVED",
                }
                return [
                    dict(base, id=11, version_no=1),
                    dict(base, id=12, version_no=2),
                ]

        query = KnowledgeQuery(
            age_band=None, topics=["动物"], task_type="GUIDANCE", query_text="",
        )
        result = DuplicateRetriever().retrieve(query)
        self.assertEqual([item.knowledge_item_id for item in result.items], [12])


class PromptTests(G09DatabaseMixin, unittest.TestCase):
    def test_prompt_uses_shared_request_and_keeps_knowledge_as_context(self):
        retrieval = KnowledgeRetriever(self.db_path).retrieve(KnowledgeQuery(
            age_band="AGE_3_4", topics=["动物"], task_type="RETRIEVAL",
            query_text="雨伞",
        ))
        request = build_prompt(
            "CONTEXT_QUESTION", age_band="AGE_3_4", page_text="小兔子把雨伞借给小熊。",
            items=retrieval.items, user_input="围绕当前页提一个问题。",
        )
        self.assertIsInstance(request, ModelGenerateRequest)
        self.assertIn("3～6岁", request.system_prompt)
        self.assertIn("不得编造", request.system_prompt)
        self.assertIn("小兔子把雨伞借给小熊。", request.context_text)
        self.assertIn("KN-STORY-001", request.context_text)
        self.assertNotIn("outputText", request.system_prompt)
        self.assertTrue(0 < len(request.user_input) <= 200)

    def test_prompt_rejects_empty_or_too_long_user_input(self):
        for user_input in ("", "字" * 201):
            with self.subTest(user_input=user_input), self.assertRaises(ValueError):
                build_prompt(
                    "STORY_ANSWER", age_band="AGE_4_5", page_text="当前页。",
                    items=[], user_input=user_input,
                )


class FakeAdapter:
    def __init__(self, result: ModelGenerateResult):
        self.result = result
        self.calls: list[ModelGenerateRequest] = []

    async def generate(self, request: ModelGenerateRequest) -> ModelGenerateResult:
        self.calls.append(request)
        return self.result


class G09ContentServiceTests(G09DatabaseMixin, unittest.IsolatedAsyncioTestCase):
    def _service(self, adapter):
        from app.agent import ContentGenerator

        return G09ContentService(
            KnowledgeRetriever(self.db_path), ContentGenerator(adapter),
        )

    async def test_success_draft_is_intermediate_and_carries_sources(self):
        adapter = FakeAdapter(ModelGenerateResult(success=True, text="小兔子帮助了谁？"))
        draft = await self._service(adapter).generate(
            "CONTEXT_QUESTION", age_band="AGE_3_4", page_text="小兔子把雨伞借给小熊。",
            user_input="围绕当前页提一个问题。", topics=["动物"], query_text="雨伞",
        )
        self.assertNotIsInstance(draft, AgentResult)
        self.assertFalse(hasattr(draft, "status"))
        self.assertFalse(hasattr(draft, "strategyType"))
        self.assertEqual(draft.text, "小兔子帮助了谁？")
        self.assertTrue(draft.has_knowledge)
        self.assertIsNone(draft.model_error_code)
        self.assertEqual([source.sourceTitle for source in draft.sources],
                         ["知芽｜知识库知识条目 v2.5", "知芽｜知识库知识条目 v2.5"])
        self.assertEqual(len(adapter.calls), 1)

    async def test_failure_keeps_error_code_and_empty_text(self):
        adapter = FakeAdapter(ModelGenerateResult(success=False, error_code="API_ERROR"))
        # Use a topic that actually matches approved knowledge so the model is called.
        draft = await self._service(adapter).generate(
            "RECOMMENDATION_REASON", age_band=None, page_text="当前页。",
            user_input="说明理由。", topics=["动物"], query_text="雨伞",
        )
        self.assertEqual(draft.text, "")
        self.assertEqual(draft.model_error_code, "API_ERROR")
        self.assertTrue(draft.has_knowledge)
        self.assertEqual(len(adapter.calls), 1)

    async def test_no_approved_knowledge_short_circuits_model_call(self):
        """Empty / all-DRAFT / missing DB: never call the model, return no-knowledge draft."""
        from app.agent import ContentGenerator

        scenarios = {
            "all_draft": self.db_path,
            "missing_db": Path(self._tmp.name) / "missing.sqlite3",
        }
        for name, db_path in scenarios.items():
            with self.subTest(scenario=name):
                if name == "all_draft":
                    with closing(sqlite3.connect(db_path)) as db, db:
                        db.execute("UPDATE knowledge_items SET status = 'DRAFT'")
                adapter = FakeAdapter(ModelGenerateResult(success=True, text="不该被调用"))
                service = G09ContentService(
                    KnowledgeRetriever(db_path), ContentGenerator(adapter),
                )
                draft = await service.generate(
                    "CONTEXT_QUESTION", age_band="AGE_3_4", page_text="当前页。",
                    user_input="提个问题。", topics=["动物"], query_text="雨伞",
                )
                self.assertEqual(adapter.calls, [])
                self.assertFalse(draft.has_knowledge)
                self.assertEqual(draft.text, "")
                self.assertEqual(draft.sources, [])
                self.assertIsNone(draft.model_error_code)

    async def test_sources_match_prompt_after_max_items_truncation(self):
        """sources must list only the items actually placed in the model context."""
        adapter = FakeAdapter(ModelGenerateResult(success=True, text="回答。"))
        draft = await self._service(adapter).generate(
            "CONTEXT_QUESTION", age_band="AGE_3_4", page_text="当前页。",
            user_input="提个问题。", topics=["动物"], query_text="雨伞", max_items=1,
        )
        self.assertEqual(len(adapter.calls), 1)
        prompt_context = adapter.calls[0].context_text
        # max_items=1 -> only one knowledge block was sent to the model.
        self.assertIn("KN-STORY-001", prompt_context)
        self.assertNotIn("KN-AGE-001", prompt_context)
        # sources carry exactly that one item, not the full retrieval list.
        self.assertEqual(len(draft.sources), 1)
        self.assertEqual(draft.sources[0].sourceTitle, "知芽｜知识库知识条目 v2.5")
        self.assertEqual(draft.sources[0].knowledgeItemId, 3)  # KN-STORY-001

    async def test_nonpositive_max_items_skips_model_with_matching_knowledge(self):
        adapter = FakeAdapter(ModelGenerateResult(success=True, text="回答。"))
        service = self._service(adapter)
        for limit in (0, -1):
            with self.subTest(max_items=limit):
                draft = await service.generate(
                    "CONTEXT_QUESTION", age_band="AGE_3_4", page_text="当前页。",
                    topics=["动物"], user_input="提个问题。", max_items=limit,
                )
                self.assertEqual(adapter.calls, [])
                self.assertFalse(draft.has_knowledge)
                self.assertEqual(draft.sources, [])
                self.assertEqual(draft.text, "")
                self.assertIsNone(draft.model_error_code)
                self.assertNotIn("KN-", draft.request.context_text)

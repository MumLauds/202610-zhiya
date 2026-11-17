"""G09 knowledge retrieval over the shared PR #27 agent schema."""

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from app.core.config import database_path
from app.schemas.agent import KnowledgeItem, KnowledgeQuery, KnowledgeRetrievalResult

APPROVED = "APPROVED"
TOKEN_PATTERN = re.compile(r"[\w\u4e00-\u9fff]+")


def _json_list(raw: str) -> list[str]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _tokens(text: str) -> list[str]:
    return [token for token in TOKEN_PATTERN.findall(text.lower()) if token]


def _score(row: sqlite3.Row, query: KnowledgeQuery, topics: set[str]) -> int:
    score = 0
    score += 3 * len(topics.intersection(set(query.topics)))
    content = f"{row['title']} {row['content']}".lower()
    for token in _tokens(query.query_text):
        if token in content:
            score += 2
    if row["age_band"] is None:
        score += 1
    return score


class KnowledgeRetriever:
    """Read-only retrieval of approved knowledge items.

    Age filter follows DCL-003: a ``None`` age_band means an all-age query and
    only matches all-age items; a concrete age band also matches all-age items.
    Only the latest approved version of each item_key is returned.
    """

    def __init__(self, path: Path | None = None, *, limit: int = 5) -> None:
        if limit <= 0:
            raise ValueError("limit must be positive")
        self._path = path
        self.limit = limit

    def _db_path(self) -> Path:
        return self._path if self._path is not None else database_path()

    def _rows(self) -> list[sqlite3.Row]:
        try:
            with closing(sqlite3.connect(f"{self._db_path().as_uri()}?mode=ro", uri=True, timeout=1)) as db:
                db.row_factory = sqlite3.Row
                return list(db.execute(
                    "SELECT id, version_no, item_key, title, source_title, source_ref, "
                    "age_band, topics_json, task_types_json, content, status "
                    "FROM knowledge_items WHERE status = ?",
                    (APPROVED,),
                ))
        except sqlite3.Error:
            return []

    def retrieve(self, query: KnowledgeQuery) -> KnowledgeRetrievalResult:
        topics = set(query.topics)
        candidates: list[tuple[int, sqlite3.Row, set[str]]] = []
        for row in self._rows():
            if query.age_band is None:
                if row["age_band"] is not None:
                    continue
            elif row["age_band"] not in (None, query.age_band):
                continue
            row_topics = set(_json_list(row["topics_json"]))
            if topics and not topics.intersection(row_topics):
                continue
            if query.task_type not in _json_list(row["task_types_json"]):
                continue
            candidates.append((_score(row, query, row_topics), row, row_topics))

        candidates.sort(key=lambda entry: (-entry[0], -entry[1]["version_no"], -entry[1]["id"]))

        items: list[KnowledgeItem] = []
        seen_keys: set[str] = set()
        for _, row, _ in candidates:
            if row["item_key"] in seen_keys:
                continue
            seen_keys.add(row["item_key"])
            items.append(KnowledgeItem(
                knowledge_item_id=row["id"],
                item_key=row["item_key"],
                content=row["content"],
                source_title=row["source_title"],
                source_ref=row["source_ref"],
            ))
            if len(items) >= self.limit:
                break
        return KnowledgeRetrievalResult(items=items, has_results=bool(items))


def retrieve_knowledge(query: KnowledgeQuery, *, path: Path | None = None,
                       limit: int = 5) -> KnowledgeRetrievalResult:
    """Convenience wrapper for callers that do not need to keep a retriever."""
    return KnowledgeRetriever(path, limit=limit).retrieve(query)

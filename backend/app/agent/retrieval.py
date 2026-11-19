"""G09 knowledge retrieval over the shared PR #27 agent schema."""

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from app.core.config import database_path
from app.schemas.agent import KnowledgeItem, KnowledgeQuery, KnowledgeRetrievalResult

APPROVED = "APPROVED"
WORD_PATTERN = re.compile(r"[a-z0-9_]+")
CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]+")
# Common Chinese function characters used only to split noisy n-grams.
CJK_STOP_CHARS = frozenset(
    "的了着过在是是把给被为什与及以于之其这那些个我们你们他们它"
    "呢吗啊吧呀哦嗯也都就才还又很从对向所此而且则即如"
)


def _json_list(raw: str) -> list[str]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _tokens(text: str) -> list[str]:
    """Split a query into matchable keywords, including contiguous Chinese.

    Chinese input usually arrives as a full sentence with no spaces. Treating
    the whole run as one token makes keyword relevance collapse into
    topic/age/id ordering. For each CJK run we keep:
      * segments split on function characters (e.g. 小兔子|雨伞|小熊), and
      * 2-3 character sliding fragments that are not pure stop characters.
    ASCII words/digits stay intact. Results are de-duplicated, order preserved.
    """
    lowered = text.lower()
    tokens: list[str] = list(WORD_PATTERN.findall(lowered))
    stop_class = "[" + "".join(sorted(CJK_STOP_CHARS)) + "]+"
    for chunk in CJK_PATTERN.findall(lowered):
        for segment in re.split(stop_class, chunk):
            # Keep non-stop single characters too (e.g. 伞, 雨, 火). They are
            # already split on stop characters, so a non-empty segment is a
            # meaningful keyword even when it is one character long.
            if segment:
                tokens.append(segment)
        for size in (2, 3):
            for index in range(len(chunk) - size + 1):
                fragment = chunk[index:index + size]
                if any(char not in CJK_STOP_CHARS for char in fragment):
                    tokens.append(fragment)
    seen: set[str] = set()
    unique: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            unique.append(token)
    return unique


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
        keywords = _tokens(query.query_text)
        candidates: list[tuple[tuple[int, int, bool, int, int], sqlite3.Row]] = []
        for row in self._rows():
            if query.age_band is None:
                if row["age_band"] is not None:
                    continue
            elif row["age_band"] not in (None, query.age_band):
                continue
            topic_matches = len(topics.intersection(_json_list(row["topics_json"])))
            if topics and not topic_matches:
                continue
            if query.task_type not in _json_list(row["task_types_json"]):
                continue
            content = f"{row['title']} {row['content']}".lower()
            keyword_matches = sum(keyword in content for keyword in keywords)
            rank = (topic_matches, keyword_matches, row["age_band"] is None, row["version_no"], row["id"])
            candidates.append((rank, row))

        candidates.sort(key=lambda entry: entry[0], reverse=True)

        items: list[KnowledgeItem] = []
        seen_keys: set[str] = set()
        for _, row in candidates:
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

"""Check the optional course drafts through the admin API in an isolated database."""
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import unquote, urlsplit

from fastapi.testclient import TestClient
from PIL import Image

from app.agent.retrieval import KnowledgeRetriever
from app.main import app
from app.schemas.agent import KnowledgeQuery

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / 'data' / 'seed'
BOOK_IDS = (2, 3, 4, 6, 9, 12, 18, 20)


def read_seed(name):
    return json.loads((SEED / name).read_text(encoding='utf-8'))


def test_course_draft_sources_and_media():
    drafts = read_seed('course_knowledge_drafts.json')
    books = {row['id']: row for row in read_seed('story_versions.json')}
    covers = {row['storybook_id']: row['cover_path'] for row in read_seed('cover_mapping.json')}
    pages = read_seed('story_pages.json')
    assert [item['itemKey'] for item in drafts] == [f'KN-BOOK-{i:03}' for i in BOOK_IDS]
    for book_id, item in zip(BOOK_IDS, drafts, strict=True):
        book = books[book_id]
        assert item['ageBand'] == f"AGE_{book['age_min']}_{book['age_max']}"
        assert set(item['topicTags']) <= set(book['topics_json'])
        assert item['status'] == 'DRAFT'
        url = urlsplit(item['sourceReference'])
        assert url.netloc == 'github.com'
        docpath = re.fullmatch(r'/MumLauds/202610-zhiya/blob/[a-f0-9]{40}/(knowledge-base_v2\.5/绘本样例集_v2\.5\.md)', unquote(url.path))
        first, last = map(int, re.fullmatch(r'L(\d+)-L(\d+)', url.fragment).groups())
        source_lines = (ROOT / docpath.group(1)).read_text(encoding='utf-8').splitlines()[first-1:last]
        rows = sorted((p for p in pages if p['version_id'] == book_id), key=lambda p: p['page_no'])
        assert [line.split('|')[2].strip() for line in source_lines] == [p['body_text'] for p in rows]
        for image in [covers[book_id], *(p['image_ref'] for p in rows)]:
            with Image.open(ROOT / image) as picture:
                assert picture.format == 'PNG'
                picture.load()


def test_course_drafts_require_review_before_matching_book_age_topic_and_task(tmp_path, monkeypatch):
    db_path = tmp_path / 'course.sqlite3'
    monkeypatch.setenv('ZHIYA_DB_PATH', str(db_path))
    monkeypatch.syspath_prepend(str(ROOT / 'database'))
    from import_seed import import_seed
    from init_db import initialize
    initialize(db_path)
    assert import_seed(db_path)
    books = {row['id']: row for row in read_seed('story_versions.json')}
    first_pages = {row['version_id']: row['body_text'] for row in read_seed('story_pages.json') if row['page_no'] == 1}
    drafts = read_seed('course_knowledge_drafts.json')
    path = '/api/v1/admin/knowledge-items'
    credentials = {'account': 'course-review-fixture', 'password': 'temporary-fixture-2026'}
    retriever = KnowledgeRetriever(db_path)
    with TestClient(app, base_url='https://testserver') as client:
        assert client.post('/api/v1/auth/registrations', json=credentials).status_code == 201
        with closing(sqlite3.connect(db_path)) as db, db:
            db.execute("UPDATE accounts SET role='ADMIN' WHERE account=?", (credentials['account'],))
        assert client.post('/api/v1/auth/sessions', json=credentials).status_code == 201
        csrf = {'X-CSRF-Token': client.cookies['zhiya_csrf']}
        ids = []
        for item in drafts:
            headers = {**csrf, 'Idempotency-Key': f"course-v1-{item['itemKey']}"}
            created = client.post(path, json=item, headers=headers)
            assert created.status_code == 201, created.text
            saved = created.json()['data']
            assert saved['status'] == 'DRAFT'
            replay = client.post(path, json=item, headers=headers)
            assert replay.json()['data']['knowledgeItemId'] == saved['knowledgeItemId']
            ids.append(saved['knowledgeItemId'])
        assert len(set(ids)) == len(BOOK_IDS)
        for review in (False, True):
            if review:
                for knowledge_id in ids:
                    response = client.patch(f'{path}/{knowledge_id}', headers=csrf, json={'status': 'APPROVED'})
                    assert response.status_code == 200
                    assert response.json()['data']['versionNumber'] == 2
            for book_id, draft in zip(BOOK_IDS, drafts, strict=True):
                book = books[book_id]
                for task in ('GUIDANCE', 'RETRIEVAL'):
                    query = KnowledgeQuery(
                        age_band=f"AGE_{book['age_min']}_{book['age_max']}", topics=book['topics_json'],
                        task_type=task, query_text=first_pages[book_id],
                    )
                    hits = retriever.retrieve(query).items
                    expected = [(draft['itemKey'], draft['sourceReference'])] if review else []
                    assert [(hit.item_key, hit.source_ref) for hit in hits] == expected
                    if review:
                        for changed in ({'age_band': None}, {'topics': ['不相关主题']}, {'task_type': 'SAFETY'}):
                            assert not retriever.retrieve(query.model_copy(update=changed)).has_results
                        wrong_age = 'AGE_4_5' if query.age_band == 'AGE_3_4' else 'AGE_3_4'
                        others = retriever.retrieve(query.model_copy(update={'age_band': wrong_age})).items
                        assert draft['itemKey'] not in {hit.item_key for hit in others}

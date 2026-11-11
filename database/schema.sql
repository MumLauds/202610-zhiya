-- Field dictionary and indexes: database design v1.1, sections 4.3.1–4.3.8.
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY CHECK (id BETWEEN 1 AND 9007199254740991),
    account TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('GUARDIAN', 'ADMIN')),
    created_at INTEGER NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS auth_sessions (
    id INTEGER PRIMARY KEY CHECK (id BETWEEN 1 AND 9007199254740991),
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    token_digest TEXT NOT NULL UNIQUE,
    expires_at INTEGER NOT NULL,
    revoked_at INTEGER
) STRICT;

CREATE TABLE IF NOT EXISTS child_profiles (
    id INTEGER PRIMARY KEY CHECK (id BETWEEN 1 AND 9007199254740991),
    guardian_id INTEGER NOT NULL REFERENCES accounts(id),
    nickname TEXT,
    age_band TEXT NOT NULL CHECK (age_band IN ('AGE_3_4', 'AGE_4_5', 'AGE_5_6')),
    interests_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(interests_json) AND json_type(interests_json) = 'array'),
    preferred_minutes INTEGER NOT NULL CHECK (preferred_minutes BETWEEN 1 AND 30),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS storybooks (
    id INTEGER PRIMARY KEY CHECK (id BETWEEN 1 AND 9007199254740991),
    owner_guardian_id INTEGER REFERENCES accounts(id),
    source_type TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    CHECK ((source_type = 'LIBRARY' AND owner_guardian_id IS NULL)
        OR (source_type = 'ORIGINAL' AND owner_guardian_id IS NOT NULL))
) STRICT;

CREATE TABLE IF NOT EXISTS story_versions (
    id INTEGER PRIMARY KEY CHECK (id BETWEEN 1 AND 9007199254740991),
    storybook_id INTEGER NOT NULL REFERENCES storybooks(id),
    version_no INTEGER NOT NULL CHECK (version_no > 0),
    title TEXT NOT NULL,
    topics_json TEXT NOT NULL CHECK (json_valid(topics_json) AND json_type(topics_json) = 'array'),
    age_min INTEGER NOT NULL CHECK (age_min >= 3),
    age_max INTEGER NOT NULL CHECK (age_max BETWEEN age_min AND 6),
    source_ref TEXT,
    license_note TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'DRAFT', 'CHECK_FAILED', 'PENDING_REVIEW', 'APPROVED_PRIVATE', 'PUBLISHED', 'RETIRED', 'WITHDRAWN'
    )),
    reviewed_by_id INTEGER REFERENCES accounts(id),
    reviewed_at INTEGER,
    created_at INTEGER NOT NULL,
    source_version_id INTEGER REFERENCES story_versions(id),
    source_session_id INTEGER REFERENCES reading_sessions(id),
    source_revision INTEGER CHECK (source_revision >= 0),
    source_page_no INTEGER CHECK (source_page_no > 0),
    resume_page_no INTEGER CHECK (resume_page_no > 0),
    source_visited_pages_json TEXT CHECK (
        json_valid(source_visited_pages_json) AND json_type(source_visited_pages_json) = 'array'
    ),
    branching INTEGER NOT NULL CHECK (branching IN (0, 1)),
    UNIQUE (storybook_id, version_no),
    CHECK (status NOT IN ('APPROVED_PRIVATE', 'PUBLISHED')
        OR (reviewed_by_id IS NOT NULL AND reviewed_at IS NOT NULL)),
    CHECK (
        (source_version_id IS NULL AND source_session_id IS NULL AND source_revision IS NULL
            AND source_page_no IS NULL AND resume_page_no IS NULL AND source_visited_pages_json IS NULL)
        OR
        (source_version_id IS NOT NULL AND source_session_id IS NOT NULL AND source_revision IS NOT NULL
            AND source_page_no IS NOT NULL AND resume_page_no IS NOT NULL AND source_visited_pages_json IS NOT NULL)
    )
) STRICT;

CREATE TABLE IF NOT EXISTS story_pages (
    version_id INTEGER NOT NULL REFERENCES story_versions(id),
    page_no INTEGER NOT NULL CHECK (page_no > 0),
    body_text TEXT NOT NULL,
    image_ref TEXT NOT NULL,
    audio_ref TEXT,
    image_license_note TEXT,
    default_next_page_number INTEGER,
    PRIMARY KEY (version_id, page_no),
    FOREIGN KEY (version_id, default_next_page_number)
        REFERENCES story_pages(version_id, page_no) DEFERRABLE INITIALLY DEFERRED
) STRICT;

CREATE TABLE IF NOT EXISTS story_branches (
    id INTEGER PRIMARY KEY CHECK (id BETWEEN 1 AND 9007199254740991),
    version_id INTEGER NOT NULL REFERENCES story_versions(id),
    from_page_no INTEGER NOT NULL,
    next_page_no INTEGER NOT NULL,
    choice_label TEXT NOT NULL,
    review_status TEXT NOT NULL CHECK (review_status IN ('PENDING', 'APPROVED', 'REJECTED')),
    UNIQUE (id, version_id, from_page_no),
    UNIQUE (version_id, from_page_no, choice_label),
    FOREIGN KEY (version_id, from_page_no) REFERENCES story_pages(version_id, page_no),
    FOREIGN KEY (version_id, next_page_no) REFERENCES story_pages(version_id, page_no)
) STRICT;

CREATE TABLE IF NOT EXISTS child_grants (
    id INTEGER PRIMARY KEY CHECK (id BETWEEN 1 AND 9007199254740991),
    profile_id INTEGER NOT NULL REFERENCES child_profiles(id),
    issued_by_session_id INTEGER NOT NULL UNIQUE REFERENCES auth_sessions(id),
    token_digest TEXT NOT NULL UNIQUE,
    issued_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL CHECK (expires_at - issued_at BETWEEN 60000 AND 1800000),
    revoked_at INTEGER,
    UNIQUE (id, profile_id)
) STRICT;

CREATE TABLE IF NOT EXISTS child_grant_books (
    grant_id INTEGER NOT NULL REFERENCES child_grants(id),
    version_id INTEGER NOT NULL REFERENCES story_versions(id),
    PRIMARY KEY (grant_id, version_id)
) STRICT;

CREATE TABLE IF NOT EXISTS reading_sessions (
    id INTEGER PRIMARY KEY CHECK (id BETWEEN 1 AND 9007199254740991),
    profile_id INTEGER NOT NULL REFERENCES child_profiles(id),
    version_id INTEGER NOT NULL REFERENCES story_versions(id),
    grant_id INTEGER,
    current_page_no INTEGER,
    mode TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('CREATED', 'IN_PROGRESS', 'FINISHED', 'INTERRUPTED')),
    end_reason TEXT,
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    visited_pages_json TEXT NOT NULL DEFAULT '[]' CHECK (
        json_valid(visited_pages_json) AND json_type(visited_pages_json) = 'array'
    ),
    source_reading_session_id INTEGER UNIQUE REFERENCES reading_sessions(id) ON DELETE SET NULL,
    start_page_number INTEGER NOT NULL DEFAULT 1,
    carried_prefix_page_count INTEGER NOT NULL DEFAULT 0
        CHECK (carried_prefix_page_count >= 0 AND carried_prefix_page_count < start_page_number),
    created_at INTEGER NOT NULL,
    started_at INTEGER,
    ended_at INTEGER,
    UNIQUE (id, version_id),
    FOREIGN KEY (grant_id, profile_id) REFERENCES child_grants(id, profile_id),
    FOREIGN KEY (grant_id, version_id) REFERENCES child_grant_books(grant_id, version_id),
    FOREIGN KEY (version_id, current_page_no) REFERENCES story_pages(version_id, page_no),
    FOREIGN KEY (version_id, start_page_number) REFERENCES story_pages(version_id, page_no),
    CHECK ((mode = 'PARENT_CHILD' AND grant_id IS NULL) OR (mode = 'CHILD_SOLO' AND grant_id IS NOT NULL)),
    CHECK (source_reading_session_id IS NULL OR source_reading_session_id <> id),
    CHECK (status <> 'CREATED' OR started_at IS NULL),
    CHECK (status <> 'IN_PROGRESS' OR started_at IS NOT NULL),
    CHECK (started_at IS NULL OR current_page_no IS NOT NULL),
    CHECK (
        (status IN ('CREATED', 'IN_PROGRESS') AND end_reason IS NULL AND ended_at IS NULL)
        OR (status = 'FINISHED' AND ended_at IS NOT NULL AND end_reason IS NOT NULL
            AND end_reason IN ('USER_FINISH', 'TIME_LIMIT', 'CONTINUED'))
        OR (status = 'INTERRUPTED' AND ended_at IS NOT NULL AND end_reason IS NOT NULL
            AND end_reason IN ('USER_CANCEL', 'GRANT_REVOKED', 'CONTENT_WITHDRAWN'))
    )
) STRICT;

CREATE TABLE IF NOT EXISTS interaction_events (
    id INTEGER PRIMARY KEY CHECK (id BETWEEN 1 AND 9007199254740991),
    session_id INTEGER NOT NULL,
    version_id INTEGER NOT NULL,
    request_key TEXT NOT NULL CHECK (length(request_key) BETWEEN 1 AND 64),
    request_digest TEXT NOT NULL,
    response_json TEXT NOT NULL CHECK (json_valid(response_json) AND json_type(response_json) = 'object'),
    page_no INTEGER NOT NULL,
    branch_id INTEGER,
    event_type TEXT NOT NULL CHECK (event_type IN (
        'STARTED', 'PAGE_CHANGED', 'QUESTION', 'ANSWER', 'ASK', 'FEEDBACK', 'BRANCH_CHOSEN'
    )),
    feedback TEXT CHECK (feedback IN ('UNDERSTOOD', 'NOT_UNDERSTOOD', 'SKIPPED')),
    input_text TEXT CHECK (length(trim(input_text)) BETWEEN 1 AND 200),
    output_text TEXT,
    strategy_type TEXT,
    result_status TEXT NOT NULL CHECK (result_status IN ('SUCCESS', 'FALLBACK', 'FAILED')),
    error_code TEXT,
    revision_after INTEGER NOT NULL CHECK (revision_after >= 0),
    created_at INTEGER NOT NULL,
    UNIQUE (session_id, request_key),
    FOREIGN KEY (session_id, version_id) REFERENCES reading_sessions(id, version_id),
    FOREIGN KEY (version_id, page_no) REFERENCES story_pages(version_id, page_no),
    FOREIGN KEY (branch_id, version_id, page_no) REFERENCES story_branches(id, version_id, from_page_no)
) STRICT;

CREATE TABLE IF NOT EXISTS reading_reports (
    session_id INTEGER PRIMARY KEY REFERENCES reading_sessions(id),
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'READY', 'FAILED')),
    summary TEXT,
    activities_json TEXT CHECK (activities_json IS NULL OR
        (json_valid(activities_json) AND json_type(activities_json) = 'array')),
    updated_at INTEGER NOT NULL,
    CHECK (status <> 'READY' OR summary IS NOT NULL)
) STRICT;

CREATE TABLE IF NOT EXISTS request_executions (
    id INTEGER PRIMARY KEY CHECK (id BETWEEN 1 AND 9007199254740991),
    owner_account_id INTEGER NOT NULL REFERENCES accounts(id),
    session_id INTEGER REFERENCES reading_sessions(id),
    scope_type TEXT NOT NULL CHECK (scope_type IN ('SESSION_EVENT', 'STORY_GENERATION', 'SESSION_CREATE')),
    scope_id INTEGER NOT NULL,
    client_request_id TEXT NOT NULL CHECK (length(client_request_id) BETWEEN 1 AND 64),
    request_digest TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'UNKNOWN')),
    execution_token TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    deadline_at INTEGER NOT NULL CHECK (deadline_at > started_at),
    completed_at INTEGER,
    response_status INTEGER,
    response_body TEXT CHECK (response_body IS NULL OR
        (json_valid(response_body) AND json_type(response_body) = 'object')),
    UNIQUE (scope_type, scope_id, client_request_id),
    CHECK (
        (scope_type = 'SESSION_EVENT' AND session_id IS NOT NULL AND scope_id = session_id)
        OR (scope_type <> 'SESSION_EVENT' AND session_id IS NULL AND scope_id = owner_account_id)
    ),
    CHECK (status = 'RUNNING' OR
        (completed_at IS NOT NULL AND response_status IS NOT NULL AND response_body IS NOT NULL))
) STRICT;

CREATE TABLE IF NOT EXISTS knowledge_items (
    id INTEGER PRIMARY KEY CHECK (id BETWEEN 1 AND 9007199254740991),
    version_no INTEGER NOT NULL CHECK (version_no > 0),
    item_key TEXT NOT NULL,
    title TEXT NOT NULL,
    source_title TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    locator TEXT NOT NULL,
    age_band TEXT CHECK (age_band IN ('AGE_3_4', 'AGE_4_5', 'AGE_5_6')),
    topics_json TEXT NOT NULL CHECK (json_valid(topics_json) AND json_type(topics_json) = 'array'),
    task_types_json TEXT NOT NULL CHECK (json_valid(task_types_json) AND json_type(task_types_json) = 'array'),
    content TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('DRAFT', 'APPROVED', 'RETIRED')),
    reviewed_at INTEGER,
    created_at INTEGER NOT NULL,
    UNIQUE (item_key, version_no),
    CHECK (status <> 'APPROVED' OR reviewed_at IS NOT NULL)
) STRICT;

CREATE TABLE IF NOT EXISTS event_evidence (
    event_id INTEGER NOT NULL REFERENCES interaction_events(id),
    knowledge_item_id INTEGER NOT NULL REFERENCES knowledge_items(id),
    rank_no INTEGER NOT NULL CHECK (rank_no > 0),
    PRIMARY KEY (event_id, knowledge_item_id),
    UNIQUE (event_id, rank_no)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_child_profiles_guardian ON child_profiles(guardian_id);
CREATE INDEX IF NOT EXISTS idx_reading_sessions_profile_created ON reading_sessions(profile_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_interaction_events_session_created ON interaction_events(session_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_story_versions_status_age ON story_versions(status, age_min, age_max);
CREATE INDEX IF NOT EXISTS idx_request_executions_status_deadline ON request_executions(status, deadline_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_story_versions_active ON story_versions(storybook_id)
    WHERE status IN ('PUBLISHED', 'APPROVED_PRIVATE');
CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_items_active ON knowledge_items(item_key)
    WHERE status = 'APPROVED';

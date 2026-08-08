-- ============================================================
-- Contract Review System — Full Schema
-- ============================================================

-- Users table (base system)
CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    username        VARCHAR(128) NOT NULL UNIQUE,
    password_hash   VARCHAR(256) NOT NULL,
    role            VARCHAR(32) NOT NULL DEFAULT 'user'
                    CHECK (role IN ('user', 'admin')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- Contract Review tables (5 new tables)
-- ============================================================

-- ① Main review task table
CREATE TABLE IF NOT EXISTS contract_reviews (
    id                  SERIAL PRIMARY KEY,
    user_id             INTEGER NOT NULL REFERENCES users(id),
    filename            VARCHAR(512) NOT NULL,
    original_filename   VARCHAR(512) NOT NULL,
    contract_type       VARCHAR(64),
    status              VARCHAR(32) NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','parsing','reviewing','retrieving','revising','completed','failed')),
    disclaimer_accepted BOOLEAN NOT NULL DEFAULT FALSE,
    lawyer_confirmed_at TIMESTAMPTZ,
    error_message       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ② Clauses — output of clause_parser node
CREATE TABLE IF NOT EXISTS contract_clauses (
    id              SERIAL PRIMARY KEY,
    review_id       INTEGER NOT NULL REFERENCES contract_reviews(id) ON DELETE CASCADE,
    clause_id       VARCHAR(64) NOT NULL,
    seq_no          INTEGER NOT NULL,
    type            VARCHAR(64),
    title           VARCHAR(512),
    content         TEXT NOT NULL,
    page            INTEGER,
    char_start      INTEGER,
    char_end        INTEGER,
    span            JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_clauses_review ON contract_clauses(review_id);
CREATE INDEX IF NOT EXISTS idx_clauses_clause_id ON contract_clauses(clause_id);

-- ②.5 Review cards table (output of multi_dim_review)
CREATE TABLE IF NOT EXISTS contract_review_cards (
    id              SERIAL PRIMARY KEY,
    review_id       INTEGER NOT NULL REFERENCES contract_reviews(id) ON DELETE CASCADE,
    clause_id       VARCHAR(64) NOT NULL,
    dimension       VARCHAR(64) NOT NULL,
    score           FLOAT DEFAULT 0.0,
    level           VARCHAR(16) NOT NULL DEFAULT '无',
    span            VARCHAR(32) DEFAULT '',
    suggestion      TEXT DEFAULT '',
    risk_type       VARCHAR(128) DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cards_review ON contract_review_cards(review_id);
CREATE INDEX IF NOT EXISTS idx_cards_clause ON contract_review_cards(clause_id);

-- ③ Evidence — output of rag_retriever node (source_id NOT NULL)
CREATE TABLE IF NOT EXISTS contract_evidence (
    id                  SERIAL PRIMARY KEY,
    review_id           INTEGER NOT NULL REFERENCES contract_reviews(id) ON DELETE CASCADE,
    clause_id           VARCHAR(64) NOT NULL,
    source_id           VARCHAR(256) NOT NULL,
    source_collection   VARCHAR(64) NOT NULL
                        CHECK (source_collection IN ('kb_law','kb_case','kb_template','civil_code_contract','civil_code_hybrid')),
    quote               TEXT NOT NULL,
    relevance           TEXT,
    confidence          FLOAT,
    is_human_review     BOOLEAN NOT NULL DEFAULT FALSE,
    href                VARCHAR(1024),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_evidence_review ON contract_evidence(review_id);
CREATE INDEX IF NOT EXISTS idx_evidence_source ON contract_evidence(source_id);

-- ④ Revision acceptance records (with idempotent key)
CREATE TABLE IF NOT EXISTS revision_accepts (
    id              SERIAL PRIMARY KEY,
    review_id       INTEGER NOT NULL REFERENCES contract_reviews(id) ON DELETE CASCADE,
    clause_id       VARCHAR(64) NOT NULL,
    before_text     TEXT,
    after_text      TEXT,
    diff_html       TEXT,
    evidence_ids    JSONB,
    status          VARCHAR(32) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','accepted','rejected','needs_lawyer')),
    idempotent_key  VARCHAR(128) NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_revision_review ON revision_accepts(review_id);

-- ⑤ Idempotent operations table
CREATE TABLE IF NOT EXISTS idempotent_ops (
    id              SERIAL PRIMARY KEY,
    idempotent_key  VARCHAR(128) NOT NULL UNIQUE,
    operation_type  VARCHAR(64) NOT NULL,
    status          VARCHAR(32) NOT NULL DEFAULT 'processing'
                    CHECK (status IN ('processing','completed','failed')),
    result          JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_idempotent_key ON idempotent_ops(idempotent_key);

-- ============================================================
-- Contract QA module tables (migration 002)
-- ============================================================

-- ⑥ QA sessions — one contract may open multiple Q&A sessions
CREATE TABLE IF NOT EXISTS contract_qa_sessions (
    id           SERIAL PRIMARY KEY,
    contract_id  INTEGER NOT NULL REFERENCES contract_reviews(id) ON DELETE CASCADE,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    title        VARCHAR(256) NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_qa_session_contract ON contract_qa_sessions(contract_id);
CREATE INDEX IF NOT EXISTS idx_qa_session_user ON contract_qa_sessions(user_id);

-- ⑦ QA messages — user questions + assistant answers (with grounded citations)
CREATE TABLE IF NOT EXISTS contract_qa_messages (
    id             SERIAL PRIMARY KEY,
    session_id     INTEGER NOT NULL REFERENCES contract_qa_sessions(id) ON DELETE CASCADE,
    role           VARCHAR(16) NOT NULL CHECK (role IN ('user','assistant')),
    content        TEXT NOT NULL DEFAULT '',
    citations      JSONB,
    status         VARCHAR(32) NOT NULL DEFAULT 'completed'
                   CHECK (status IN ('pending','streaming','completed','failed')),
    error_message  TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_qa_msg_session ON contract_qa_messages(session_id);

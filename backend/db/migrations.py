"""
Database migration module — executes DDL statements sequentially.
asyncpg does not support multi-statement SQL, so we split and run one by one.
"""
import logging
import asyncpg

from backend.config import get_settings

logger = logging.getLogger(__name__)

MIGRATIONS: list[dict] = [
    {
        "version": "001",
        "name": "contract_review_tables",
        "description": "Add 5 contract review tables + update users role CHECK",
    },
    {
        "version": "002",
        "name": "contract_qa_tables",
        "description": "Add contract QA session + message tables for the Q&A module",
    },
    {
        "version": "003",
        "name": "qa_session_summary",
        "description": "Add summary + summarized_until columns to contract_qa_sessions for rolling conversation summaries",
    },
    {
        "version": "004",
        "name": "rule_engine_and_hitl",
        "description": "Add rule engine findings table, human review decisions table, and HITL fields to contract_reviews",
    },
    {
        "version": "005",
        "name": "users_email_active_and_updated_at_trigger",
        "description": "Add email/is_active to users + auto-update updated_at triggers",
    },
]

SQL_STATEMENTS = [
    # Users table
    """
    CREATE TABLE IF NOT EXISTS users (
        id              SERIAL PRIMARY KEY,
        username        VARCHAR(128) NOT NULL UNIQUE,
        password_hash   VARCHAR(256) NOT NULL,
        role            VARCHAR(32) NOT NULL DEFAULT 'user'
                        CHECK (role IN ('user', 'admin')),
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    # contract_reviews
    """
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
    )
    """,
    # contract_clauses
    """
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
    )
    """,
    # contract_evidence
    """
    CREATE TABLE IF NOT EXISTS contract_evidence (
        id                  SERIAL PRIMARY KEY,
        review_id           INTEGER NOT NULL REFERENCES contract_reviews(id) ON DELETE CASCADE,
        clause_id           VARCHAR(64) NOT NULL,
        source_id           VARCHAR(256) NOT NULL,
        source_collection   VARCHAR(64) NOT NULL
                            CHECK (source_collection IN ('kb_law','kb_case','kb_template')),
        quote               TEXT NOT NULL,
        relevance           TEXT,
        confidence          FLOAT,
        is_human_review     BOOLEAN NOT NULL DEFAULT FALSE,
        href                VARCHAR(1024),
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    # revision_accepts
    """
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
    )
    """,
    # idempotent_ops
    """
    CREATE TABLE IF NOT EXISTS idempotent_ops (
        id              SERIAL PRIMARY KEY,
        idempotent_key  VARCHAR(128) NOT NULL UNIQUE,
        operation_type  VARCHAR(64) NOT NULL,
        status          VARCHAR(32) NOT NULL DEFAULT 'processing'
                        CHECK (status IN ('processing','completed','failed')),
        result          JSONB,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        expires_at      TIMESTAMPTZ
    )
    """,
    # contract_review_cards
    """
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
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (review_id, clause_id, dimension)
    )
    """,
    # contract_qa_sessions — Q&A module: one contract may open multiple sessions
    """
    CREATE TABLE IF NOT EXISTS contract_qa_sessions (
        id           SERIAL PRIMARY KEY,
        contract_id  INTEGER NOT NULL REFERENCES contract_reviews(id) ON DELETE CASCADE,
        user_id      INTEGER NOT NULL REFERENCES users(id),
        title        VARCHAR(256) NOT NULL DEFAULT '',
        created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    # contract_qa_messages — Q&A module: user questions + assistant answers
    """
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
    )
    """,
    # QA session rolling summary (migration 003). Idempotent: run_migrations
    # replays ALL statements for every pending version.
    "ALTER TABLE contract_qa_sessions ADD COLUMN IF NOT EXISTS summary TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE contract_qa_sessions ADD COLUMN IF NOT EXISTS summarized_until INTEGER NOT NULL DEFAULT 0",
    # ── Migration 004: Rule engine + HITL ──
    # Rule engine findings table
    """
    CREATE TABLE IF NOT EXISTS rule_findings (
        id              SERIAL PRIMARY KEY,
        review_id       INTEGER NOT NULL REFERENCES contract_reviews(id) ON DELETE CASCADE,
        rule_id         VARCHAR(20) NOT NULL,
        category        VARCHAR(30) NOT NULL,
        level           VARCHAR(10) NOT NULL,
        description     TEXT NOT NULL,
        related_clause_ids JSONB DEFAULT '[]',
        suggestion      TEXT DEFAULT '',
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    # Human review decisions table
    """
    CREATE TABLE IF NOT EXISTS human_review_decisions (
        id              SERIAL PRIMARY KEY,
        review_id       INTEGER NOT NULL REFERENCES contract_reviews(id) ON DELETE CASCADE,
        clause_id       VARCHAR(128) NOT NULL,
        action          VARCHAR(20) NOT NULL,
        modified_level  VARCHAR(10),
        modified_score  FLOAT,
        comment         TEXT DEFAULT '',
        skip_revision   BOOLEAN DEFAULT FALSE,
        decided_by      INTEGER REFERENCES users(id),
        decided_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    # HITL columns on contract_reviews
    "ALTER TABLE contract_reviews ADD COLUMN IF NOT EXISTS needs_human_review BOOLEAN DEFAULT FALSE",
    "ALTER TABLE contract_reviews ADD COLUMN IF NOT EXISTS human_review_status VARCHAR(20) DEFAULT 'skipped'",
    # Update CHECK constraint to include paused_waiting status
    """
    DO $$
    BEGIN
        ALTER TABLE contract_reviews DROP CONSTRAINT IF EXISTS contract_reviews_status_check;
        ALTER TABLE contract_reviews ADD CONSTRAINT contract_reviews_status_check
            CHECK (status IN ('pending','parsing','reviewing','retrieving','revising','paused_waiting','completed','failed'));
    EXCEPTION WHEN undefined_object THEN
        -- constraint might not exist yet; the CREATE TABLE IF NOT EXISTS will set it
        NULL;
    END $$;
    """,
    # ── Migration 005: users email/is_active + updated_at triggers ──
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(128)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE",
    # updated_at 自动维护触发器函数（BEFORE UPDATE 时置 updated_at=NOW()）
    """
    CREATE OR REPLACE FUNCTION update_updated_at_column()
    RETURNS TRIGGER AS $$
    BEGIN
        NEW.updated_at = NOW();
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    # 触发器：DROP IF EXISTS + CREATE，保证重放幂等
    "DROP TRIGGER IF EXISTS trg_users_updated_at ON users",
    "CREATE TRIGGER trg_users_updated_at BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION update_updated_at_column()",
    "DROP TRIGGER IF EXISTS trg_contract_reviews_updated_at ON contract_reviews",
    "CREATE TRIGGER trg_contract_reviews_updated_at BEFORE UPDATE ON contract_reviews FOR EACH ROW EXECUTE FUNCTION update_updated_at_column()",
    "DROP TRIGGER IF EXISTS trg_revision_accepts_updated_at ON revision_accepts",
    "CREATE TRIGGER trg_revision_accepts_updated_at BEFORE UPDATE ON revision_accepts FOR EACH ROW EXECUTE FUNCTION update_updated_at_column()",
    "DROP TRIGGER IF EXISTS trg_contract_qa_sessions_updated_at ON contract_qa_sessions",
    "CREATE TRIGGER trg_contract_qa_sessions_updated_at BEFORE UPDATE ON contract_qa_sessions FOR EACH ROW EXECUTE FUNCTION update_updated_at_column()",
]

INDEX_STATEMENTS = [
    "CREATE INDEX IF NOT EXISTS idx_clauses_review ON contract_clauses(review_id)",
    "CREATE INDEX IF NOT EXISTS idx_clauses_clause_id ON contract_clauses(clause_id)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_review ON contract_evidence(review_id)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_source ON contract_evidence(source_id)",
    "CREATE INDEX IF NOT EXISTS idx_revision_review ON revision_accepts(review_id)",
    "CREATE INDEX IF NOT EXISTS idx_idempotent_key ON idempotent_ops(idempotent_key)",
    "CREATE INDEX IF NOT EXISTS idx_cards_review ON contract_review_cards(review_id)",
    "CREATE INDEX IF NOT EXISTS idx_cards_clause ON contract_review_cards(clause_id)",
    "CREATE INDEX IF NOT EXISTS idx_qa_session_contract ON contract_qa_sessions(contract_id)",
    "CREATE INDEX IF NOT EXISTS idx_qa_session_user ON contract_qa_sessions(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_qa_msg_session ON contract_qa_messages(session_id)",
    # Migration 004 indices
    "CREATE INDEX IF NOT EXISTS idx_rule_findings_review ON rule_findings(review_id)",
    "CREATE INDEX IF NOT EXISTS idx_human_decisions_review ON human_review_decisions(review_id)",
    # Migration 005 indices
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)",
]


async def run_migrations(database_url: str) -> list[str]:
    """Execute all pending migrations. Returns list of applied version names."""
    conn = await asyncpg.connect(database_url)
    applied = []

    try:
        # Create migrations tracking table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS _migrations (
                version     VARCHAR(16) PRIMARY KEY,
                name        VARCHAR(128),
                applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        for migration in MIGRATIONS:
            existing = await conn.fetchrow(
                "SELECT version FROM _migrations WHERE version = $1", migration["version"]
            )
            if existing:
                logger.info(f"Migration {migration['version']} already applied, skipping")
                continue

            logger.info(f"Applying migration {migration['version']}: {migration['name']}")

            for stmt in SQL_STATEMENTS:
                await conn.execute(stmt)

            for stmt in INDEX_STATEMENTS:
                await conn.execute(stmt)

            await conn.execute(
                "INSERT INTO _migrations (version, name) VALUES ($1, $2)",
                migration["version"], migration["name"],
            )
            applied.append(migration["version"])
            logger.info(f"Migration {migration['version']} applied successfully")

    finally:
        await conn.close()

    return applied


def run_migrations_sync(database_url: str | None = None) -> list[str]:
    """Sync wrapper for run_migrations."""
    import asyncio

    db_url = database_url or get_settings().database_url
    return asyncio.run(run_migrations(db_url))

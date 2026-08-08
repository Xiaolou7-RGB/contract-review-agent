"""
Database migration module — executes DDL statements sequentially.
asyncpg does not support multi-statement SQL, so we split and run one by one.
"""
import logging
import asyncpg
import os

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

    db_url = database_url or os.getenv("DATABASE_URL", "postgresql://postgres:postgres123@localhost:15432/eduagent")
    return asyncio.run(run_migrations(db_url))

"""SQLite access layer. Stdlib only, no ORM."""
import sqlite3
from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name     TEXT,
    free_credits  INTEGER NOT NULL DEFAULT 0,   -- weekly-refilled, capped, non-rollover
    paid_credits  INTEGER NOT NULL DEFAULT 0,   -- purchased, never expire
    free_reset_at TEXT,                          -- last weekly free grant (ISO)
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS stripe_events (
    id         TEXT PRIMARY KEY,                 -- Stripe event id (idempotency)
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS credit_ledger (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    delta         INTEGER NOT NULL,              -- +grant/purchase/refund, -spend
    kind          TEXT NOT NULL,                 -- signup/weekly/search/refund/purchase
    balance_after INTEGER NOT NULL,
    note          TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS resumes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename    TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    text        TEXT NOT NULL,
    skills      TEXT,              -- JSON list of detected taxonomy skills
    profile     TEXT,              -- JSON: core_skills/education/prof_dev/terms
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS searches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    resume_id     INTEGER REFERENCES resumes(id) ON DELETE SET NULL,
    title_query   TEXT,
    location      TEXT,
    min_pay       INTEGER,
    work_type     TEXT,            -- any / remote / hybrid / onsite
    languages     TEXT,            -- comma-separated spoken languages
    credit_source TEXT,            -- which bucket was charged: free/paid/none
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending/running/done/error
    progress      TEXT,            -- human-readable progress line
    error         TEXT,
    result_count  INTEGER DEFAULT 0,
    export_path   TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at   TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id    INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
    match_score  INTEGER,
    ats_score    INTEGER,
    role         TEXT,
    company      TEXT,
    location     TEXT,
    salary       TEXT,
    post_status  TEXT,
    work_type    TEXT,
    why_matches  TEXT,
    missing      TEXT,
    apply_link   TEXT,
    source       TEXT,
    remote_scope TEXT,             -- allowed remote locations per posting
    flags        TEXT              -- JSON: ghost/scam/closed reasons (kept ones only)
);

CREATE TABLE IF NOT EXISTS applications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role        TEXT,
    company     TEXT,
    apply_link  TEXT,
    source      TEXT,
    match_score INTEGER,
    salary      TEXT,
    location    TEXT,
    status      TEXT NOT NULL DEFAULT 'applied',  -- saved/applied/replied/interview/offer/rejected
    applied_at  TEXT,
    replied_at  TEXT,
    notes       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_jobs_search ON jobs(search_id);
CREATE INDEX IF NOT EXISTS idx_searches_user ON searches(user_id);
CREATE INDEX IF NOT EXISTS idx_resumes_user ON resumes(user_id);
CREATE INDEX IF NOT EXISTS idx_app_user ON applications(user_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_app_user_link ON applications(user_id, apply_link);
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _column_exists(db, table, column):
    cols = [r["name"] for r in db.execute(f"PRAGMA table_info({table})").fetchall()]
    return column in cols


def _migrate(db):
    """Additive migrations for existing databases."""
    if not _column_exists(db, "searches", "languages"):
        db.execute("ALTER TABLE searches ADD COLUMN languages TEXT")
    if not _column_exists(db, "jobs", "remote_scope"):
        db.execute("ALTER TABLE jobs ADD COLUMN remote_scope TEXT")
    if not _column_exists(db, "resumes", "profile"):
        db.execute("ALTER TABLE resumes ADD COLUMN profile TEXT")
    for col, ddl in (("free_credits", "INTEGER NOT NULL DEFAULT 0"),
                     ("paid_credits", "INTEGER NOT NULL DEFAULT 0"),
                     ("free_reset_at", "TEXT")):
        if not _column_exists(db, "users", col):
            db.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
    if not _column_exists(db, "searches", "credit_source"):
        db.execute("ALTER TABLE searches ADD COLUMN credit_source TEXT")
    db.commit()


def init_db():
    db = get_db()
    db.executescript(SCHEMA)
    _migrate(db)
    db.commit()


def standalone_connection(database_path):
    """A fresh connection for use OUTSIDE the request context (background threads)."""
    conn = sqlite3.connect(database_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")  # better concurrency for the search thread
    return conn


def init_app(app):
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()

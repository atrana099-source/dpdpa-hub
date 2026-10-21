"""Tiny database layer. SQLite for dev/test, PostgreSQL for production.

SQL in this code base is written with '?' placeholders and portable types;
this module adapts it for PostgreSQL (psycopg2).
"""
import os
import sqlite3
from contextlib import contextmanager

from flask import current_app, g


def is_pg():
    return current_app.config["DATABASE_URL"].startswith(("postgres://", "postgresql://"))


def _connect(url):
    if url.startswith(("postgres://", "postgresql://")):
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
        conn.autocommit = True
        return conn
    path = url.replace("sqlite:///", "", 1)
    if path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
    return conn


def get_db():
    if "db" not in g:
        g.db = _connect(current_app.config["DATABASE_URL"])
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def _sql(sql):
    return sql.replace("?", "%s") if is_pg() else sql


def _cur(sql, params=()):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(_sql(sql), tuple(params))
    return cur


def q(sql, params=()):
    """Return a list of dict rows."""
    cur = _cur(sql, params)
    return [dict(r) for r in cur.fetchall()]


def one(sql, params=()):
    rows = q(sql, params)
    return rows[0] if rows else None


def scalar(sql, params=(), default=0):
    cur = _cur(sql, params)
    row = cur.fetchone()
    if row is None:
        return default
    val = list(dict(row).values())[0]
    return default if val is None else val


def execute(sql, params=()):
    return _cur(sql, params).rowcount


def insert(sql, params=()):
    """INSERT ... and return the new id (both SQLite >= 3.35 and PostgreSQL support RETURNING)."""
    cur = _cur(sql.rstrip().rstrip(";") + " RETURNING id", params)
    return dict(cur.fetchone())["id"]


@contextmanager
def tx():
    """Serialised write transaction (used by the append-only consent ledger)."""
    conn = get_db()
    cur = conn.cursor()
    if is_pg():
        cur.execute("BEGIN")
        cur.execute("SELECT pg_advisory_xact_lock(70011)")
    else:
        cur.execute("BEGIN IMMEDIATE")
    try:
        yield
        cur.execute("COMMIT")
    except Exception:
        cur.execute("ROLLBACK")
        raise


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id {PK}, username TEXT UNIQUE NOT NULL, name TEXT, email TEXT, role TEXT NOT NULL DEFAULT 'viewer',
  pw_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT, last_login TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS audit_log (
  id {PK}, ts TEXT NOT NULL, username TEXT, action TEXT, entity TEXT, entity_id TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS api_keys (
  id {PK}, name TEXT NOT NULL, prefix TEXT, key_hash TEXT UNIQUE NOT NULL, active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT, last_used TEXT);
CREATE TABLE IF NOT EXISTS purposes (
  id {PK}, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, description TEXT, data_categories TEXT,
  legal_basis TEXT, retention TEXT, status TEXT NOT NULL DEFAULT 'Active');
CREATE TABLE IF NOT EXISTS notices (
  id {PK}, slug TEXT NOT NULL, version INTEGER NOT NULL, title TEXT NOT NULL, language TEXT NOT NULL DEFAULT 'English',
  body TEXT NOT NULL, purpose_codes TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Draft', content_hash TEXT,
  created_at TEXT, published_at TEXT, UNIQUE (slug, version, language));
CREATE TABLE IF NOT EXISTS consent_events (
  id {PK}, receipt_id TEXT UNIQUE NOT NULL, principal_hash TEXT NOT NULL, principal_masked TEXT,
  notice_slug TEXT NOT NULL, notice_version INTEGER NOT NULL, notice_hash TEXT, event TEXT NOT NULL,
  granted TEXT NOT NULL, declined TEXT NOT NULL, channel TEXT, ip TEXT, user_agent TEXT, ts TEXT NOT NULL,
  prev_hash TEXT, hash TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_consent_principal ON consent_events (principal_hash, notice_slug);
CREATE TABLE IF NOT EXISTS dsr_requests (
  id {PK}, ref TEXT UNIQUE NOT NULL, type TEXT NOT NULL, principal_name TEXT, contact TEXT, identifier TEXT,
  description TEXT, status TEXT NOT NULL DEFAULT 'Received', received_at TEXT NOT NULL, due_at TEXT NOT NULL,
  assigned_to TEXT, identity_verified INTEGER NOT NULL DEFAULT 0, closed_at TEXT, closure_note TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS dsr_notes (
  id {PK}, dsr_id INTEGER NOT NULL, ts TEXT NOT NULL, username TEXT, note TEXT NOT NULL, to_principal INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS incidents (
  id {PK}, ref TEXT UNIQUE NOT NULL, title TEXT NOT NULL, description TEXT, aware_at TEXT NOT NULL, occurred_at TEXT,
  severity TEXT NOT NULL DEFAULT 'Medium', status TEXT NOT NULL DEFAULT 'Reported', data_categories TEXT,
  principals_affected TEXT, systems TEXT, root_cause TEXT, containment TEXT, owner TEXT,
  board_initial_at TEXT, board_detailed_at TEXT, principals_notified_at TEXT, closed_at TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS incident_log (
  id {PK}, incident_id INTEGER NOT NULL, ts TEXT NOT NULL, username TEXT, note TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ropa (
  id {PK}, code TEXT UNIQUE NOT NULL, department TEXT, activity TEXT NOT NULL, data_principals TEXT, data_categories TEXT,
  purpose TEXT, lawful_basis TEXT, systems TEXT, recipients TEXT, cross_border TEXT, retention TEXT,
  retention_gap TEXT, owner TEXT, status TEXT NOT NULL DEFAULT 'Draft', last_reviewed TEXT);
CREATE TABLE IF NOT EXISTS retention (
  id {PK}, code TEXT UNIQUE NOT NULL, data_category TEXT NOT NULL, departments TEXT, data_principals TEXT,
  clock_starts TEXT, statutory_min TEXT, proposed TEXT, basis_type TEXT, disposal TEXT, current_practice TEXT,
  ropa_ids TEXT, owner TEXT, status TEXT NOT NULL DEFAULT 'Draft', next_review TEXT);
CREATE TABLE IF NOT EXISTS gaps (
  id {PK}, code TEXT UNIQUE NOT NULL, department TEXT, theme TEXT, finding TEXT NOT NULL, type TEXT, priority TEXT,
  dpdp_ref TEXT, source TEXT, action_code TEXT, status TEXT NOT NULL DEFAULT 'Open', target_date TEXT, owner TEXT, remarks TEXT);
CREATE TABLE IF NOT EXISTS actions (
  id {PK}, code TEXT UNIQUE NOT NULL, action TEXT NOT NULL, owner_function TEXT, phase TEXT, due_date TEXT,
  departments TEXT, status TEXT NOT NULL DEFAULT 'Not started', pct INTEGER NOT NULL DEFAULT 0, named_owner TEXT,
  evidence TEXT, remarks TEXT);
CREATE TABLE IF NOT EXISTS vendors (
  id {PK}, name TEXT NOT NULL, service TEXT, data_shared TEXT, role TEXT, dpa_status TEXT NOT NULL DEFAULT 'Not signed',
  cross_border TEXT, owner TEXT, last_review TEXT, remarks TEXT);
CREATE TABLE IF NOT EXISTS departments (
  id {PK}, name TEXT UNIQUE NOT NULL, hod TEXT, interview_date TEXT, rating TEXT, declaration_signed TEXT,
  evidence_received TEXT, key_issue TEXT);
"""


def init_schema():
    pk = "SERIAL PRIMARY KEY" if is_pg() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    for stmt in SCHEMA.replace("{PK}", pk).split(";"):
        if stmt.strip():
            execute(stmt)

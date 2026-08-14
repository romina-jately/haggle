"""Append-only event log plus the minimum state to resume a thread.

The event log is the product. Everything the negotiation engine does is
reconstructable from it, and it is the training set: closes label outcome,
walks label failure, and seller overrides label judgment.

SQLite here so the repo runs with no infrastructure. The schema is boring
on purpose and moves to Postgres by changing the DSN.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

DB = Path(os.environ.get("ARINA_DB", "arina.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
  id TEXT PRIMARY KEY, retail_id TEXT UNIQUE, seller TEXT NOT NULL,
  payload TEXT NOT NULL, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS policies (
  listing_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS threads (
  id TEXT PRIMARY KEY, listing_id TEXT NOT NULL, buyer TEXT NOT NULL,
  state TEXT NOT NULL, status TEXT NOT NULL, updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL,
  listing_id TEXT, thread_id TEXT, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_thread ON events(thread_id);
CREATE INDEX IF NOT EXISTS events_kind ON events(kind);
"""


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def log(kind: str, *, listing_id: str | None = None, thread_id: str | None = None, **payload) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO events(ts, kind, listing_id, thread_id, payload) VALUES (?,?,?,?,?)",
            (time.time(), kind, listing_id, thread_id, json.dumps(payload)),
        )


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def export_events(kind: str | None = None) -> list[dict]:
    """JSONL-shaped dump. This is what the training environment reads."""
    q = "SELECT ts, kind, listing_id, thread_id, payload FROM events"
    args: tuple = ()
    if kind:
        q += " WHERE kind = ?"
        args = (kind,)
    with conn() as c:
        rows = c.execute(q + " ORDER BY id", args).fetchall()
    return [
        {"ts": r["ts"], "kind": r["kind"], "listing_id": r["listing_id"],
         "thread_id": r["thread_id"], **json.loads(r["payload"])}
        for r in rows
    ]

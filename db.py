"""SQLite database module for Kiro memory."""

import json
import math
import sqlite3
import struct
import time
from pathlib import Path
from typing import Optional

DB_PATH = Path.home() / ".kiro" / "memory" / "memory.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS semantic (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'auto',
    confidence REAL NOT NULL DEFAULT 0.8,
    embedding BLOB,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS episodic (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    importance REAL NOT NULL DEFAULT 0.5,
    embedding BLOB,
    created_at REAL NOT NULL,
    accessed_at REAL NOT NULL,
    is_deleted INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'preference',
    embedding BLOB,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_episodic_active ON episodic(is_deleted, created_at);
CREATE INDEX IF NOT EXISTS idx_semantic_key ON semantic(key);
"""


def get_db() -> sqlite3.Connection:
    """Get a database connection, creating the schema if needed."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def pack_vector(vec: list[float]) -> bytes:
    """Pack a float vector into a compact binary blob."""
    return struct.pack(f"{len(vec)}f", *vec)


def unpack_vector(blob: bytes) -> list[float]:
    """Unpack a binary blob into a float vector."""
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# --- Semantic memory ---


def write_semantic(
    conn: sqlite3.Connection,
    key: str,
    value: str,
    embedding: Optional[list[float]] = None,
    source: str = "auto",
    confidence: float = 0.8,
) -> bool:
    """Write a semantic key-value pair. Returns True if written."""
    now = time.time()
    blob = pack_vector(embedding) if embedding else None

    # Check existing - user_explicit always wins
    row = conn.execute("SELECT source, confidence FROM semantic WHERE key = ?", (key,)).fetchone()
    if row:
        if row["source"] == "user_explicit" and source != "user_explicit":
            return False  # Can't override user-explicit
        if row["confidence"] > confidence + 0.1 and source != "user_explicit":
            return False  # Higher confidence wins

    conn.execute(
        """INSERT INTO semantic (key, value, source, confidence, embedding, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET
             value=excluded.value, source=excluded.source, confidence=excluded.confidence,
             embedding=excluded.embedding, updated_at=excluded.updated_at""",
        (key, value, source, confidence, blob, now, now),
    )
    conn.commit()
    return True


def search_semantic(
    conn: sqlite3.Connection,
    query_vec: Optional[list[float]] = None,
    limit: int = 20,
    score_threshold: float = 0.0,
) -> list[dict]:
    """Search semantic memory. Vector-ranked if query_vec provided, else recency.

    Args:
        score_threshold: Minimum cosine similarity to include a result. Only applied
                         when query_vec is provided. Default 0.0 (no filtering).
    """
    rows = conn.execute(
        "SELECT key, value, source, confidence, embedding, updated_at FROM semantic"
    ).fetchall()

    results = []
    for row in rows:
        entry = {
            "key": row["key"],
            "value": row["value"],
            "source": row["source"],
            "confidence": row["confidence"],
            "updated_at": row["updated_at"],
        }
        if query_vec and row["embedding"]:
            entry["score"] = cosine_similarity(query_vec, unpack_vector(row["embedding"]))
        else:
            entry["score"] = 0.0
        results.append(entry)

    if query_vec:
        results.sort(key=lambda x: x["score"], reverse=True)
        if score_threshold > 0.0:
            results = [r for r in results if r["score"] >= score_threshold]
    else:
        results.sort(key=lambda x: x["updated_at"], reverse=True)

    return results[:limit]


# --- Episodic memory ---


def write_episodic(
    conn: sqlite3.Connection,
    text: str,
    embedding: Optional[list[float]] = None,
    tags: Optional[list[str]] = None,
    importance: float = 0.5,
) -> int:
    """Write an episodic memory. Returns the row ID."""
    now = time.time()
    blob = pack_vector(embedding) if embedding else None
    tags_json = json.dumps(tags or [])

    cur = conn.execute(
        """INSERT INTO episodic (text, tags, importance, embedding, created_at, accessed_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (text, tags_json, importance, blob, now, now),
    )
    conn.commit()
    return cur.lastrowid


def search_episodic(
    conn: sqlite3.Connection,
    query_vec: Optional[list[float]] = None,
    limit: int = 8,
    score_threshold: float = 0.0,
) -> list[dict]:
    """Search episodic memory with decay scoring.

    Decay is measured from accessed_at, so recalling a memory resets its decay clock.

    Args:
        score_threshold: Minimum combined decay score to include a result. Default 0.0.
    """
    rows = conn.execute(
        "SELECT id, text, tags, importance, embedding, created_at, accessed_at FROM episodic WHERE is_deleted = 0"
    ).fetchall()

    now = time.time()
    results = []
    for row in rows:
        # Decay from last access time — recalling a memory resets its clock
        days_since_access = (now - row["accessed_at"]) / 86400
        days_old = (now - row["created_at"]) / 86400
        importance = row["importance"]

        if query_vec and row["embedding"]:
            sim = cosine_similarity(query_vec, unpack_vector(row["embedding"]))
        else:
            sim = 0.3  # Base score for keyword fallback

        # Decay formula: sim * (0.7 + 0.3*importance) * exp(-0.03 * days_since_access)
        score = sim * (0.7 + 0.3 * importance) * math.exp(-0.03 * days_since_access)

        if score_threshold > 0.0 and score < score_threshold:
            continue

        results.append(
            {
                "id": row["id"],
                "text": row["text"],
                "tags": json.loads(row["tags"]),
                "importance": importance,
                "score": score,
                "cosine": sim if query_vec and row["embedding"] else None,
                "days_old": round(days_old, 1),
            }
        )

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]


def update_episodic_accessed(conn: sqlite3.Connection, row_ids: list[int]) -> None:
    """Update accessed_at for a list of episodic row IDs to now.

    Called after a successful Recall to reset the decay clock on retrieved memories.
    """
    if not row_ids:
        return
    now = time.time()
    conn.execute(
        f"UPDATE episodic SET accessed_at = ? WHERE id IN ({','.join('?' * len(row_ids))})",
        [now, *row_ids],
    )
    conn.commit()


# --- Lessons ---


def write_lesson(conn: sqlite3.Connection, rule: str, category: str = "preference", embedding: Optional[list[float]] = None) -> int:
    """Write a lesson (correction). Returns row ID. Deduplicates by substring."""
    # Substring dedup
    existing = conn.execute("SELECT id, rule FROM lessons").fetchall()
    for row in existing:
        if rule.lower() in row["rule"].lower() or row["rule"].lower() in rule.lower():
            # Update the existing one with the newer text
            blob = pack_vector(embedding) if embedding else None
            conn.execute(
                "UPDATE lessons SET rule = ?, category = ?, embedding = ? WHERE id = ?",
                (rule, category, blob, row["id"]),
            )
            conn.commit()
            return row["id"]

    now = time.time()
    blob = pack_vector(embedding) if embedding else None
    cur = conn.execute(
        "INSERT INTO lessons (rule, category, embedding, created_at) VALUES (?, ?, ?, ?)",
        (rule, category, blob, now),
    )
    conn.commit()
    return cur.lastrowid


def get_lessons(conn: sqlite3.Connection, category: Optional[str] = None) -> list[dict]:
    """Get all active lessons, optionally filtered by category."""
    if category:
        rows = conn.execute(
            "SELECT id, rule, category, created_at FROM lessons WHERE category = ?", (category,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT id, rule, category, created_at FROM lessons").fetchall()
    return [{"id": row["id"], "rule": row["rule"], "category": row["category"]} for row in rows]


def prune_episodic(conn: sqlite3.Connection, score_floor: float = 0.05) -> dict:
    """Hard-delete decayed and soft-deleted episodic rows.

    Removes:
    - All soft-deleted rows (is_deleted = 1).
    - Active rows whose maximum possible decay score has fallen below score_floor.
      Max possible score uses importance=1.0 and base sim=0.3 (no query context):
        0.3 * 1.0 * exp(-0.03 * days_old)

    Args:
        score_floor: Rows with max possible score below this are pruned. Default 0.05
                     (~115 days at importance=1.0).

    Returns:
        Dict with counts of soft_deleted and decayed rows removed.
    """
    now = time.time()

    # Hard-delete soft-deleted rows
    cur = conn.execute("DELETE FROM episodic WHERE is_deleted = 1")
    soft_deleted_count = cur.rowcount

    # Find active rows whose max possible decay score is below the floor
    rows = conn.execute(
        "SELECT id, importance, created_at, accessed_at FROM episodic WHERE is_deleted = 0"
    ).fetchall()

    decayed_ids = []
    for row in rows:
        days_since_access = (now - row["accessed_at"]) / 86400
        max_score = 0.3 * 1.0 * math.exp(-0.03 * days_since_access)
        if max_score < score_floor:
            decayed_ids.append(row["id"])

    if decayed_ids:
        conn.execute(
            f"DELETE FROM episodic WHERE id IN ({','.join('?' * len(decayed_ids))})",
            decayed_ids,
        )

    conn.commit()
    return {"soft_deleted": soft_deleted_count, "decayed": len(decayed_ids)}



    """Delete a semantic entry."""
    cur = conn.execute("DELETE FROM semantic WHERE key = ?", (key,))
    conn.commit()
    return cur.rowcount > 0


def delete_episodic(conn: sqlite3.Connection, row_id: int) -> bool:
    """Soft-delete an episodic entry."""
    cur = conn.execute("UPDATE episodic SET is_deleted = 1 WHERE id = ?", (row_id,))
    conn.commit()
    return cur.rowcount > 0


def delete_lesson(conn: sqlite3.Connection, row_id: int) -> bool:
    """Delete a lesson."""
    cur = conn.execute("DELETE FROM lessons WHERE id = ?", (row_id,))
    conn.commit()
    return cur.rowcount > 0

"""Kiro Memory MCP Server — persistent local memory with semantic recall."""

import json
import os
import sys
from pathlib import Path

# Ensure local modules are importable
sys.path.insert(0, str(Path(__file__).parent))

from mcp.server import MCPServer

from db import (
    get_db,
    write_semantic,
    write_episodic,
    write_lesson,
    search_semantic,
    search_episodic,
    update_episodic_accessed,
    get_lessons,
    delete_semantic,
    delete_episodic,
    delete_lesson,
    prune_episodic,
)
from embed import embed

mcp = MCPServer(name="kiro-recall")

# Auto-sync to Obsidian after writes if KIRO_MEMORY_AUTOSYNC=1
_AUTOSYNC = os.environ.get("KIRO_MEMORY_AUTOSYNC", "").lower() in ("1", "true", "yes")


def _maybe_sync():
    """Trigger Obsidian sync if auto-sync is enabled. Fails silently."""
    if not _AUTOSYNC:
        return
    try:
        from obsidian_sync import sync_all
        sync_all()
    except Exception:
        pass  # Never let a sync failure break a write operation


@mcp.tool(name="Remember")
async def remember(
    text: str,
    memory_type: str = "episodic",
    key: str = "",
    importance: float = 0.5,
    tags: str = "",
) -> str:
    """Store something in memory.

    Args:
        text: The content to remember.
        memory_type: One of 'episodic' (conversation fragment), 'semantic' (structured fact), or 'lesson' (correction/rule).
        key: Required for semantic type. Format: 'pref.theme', 'project.name', 'user.role', etc.
        importance: 0.0-1.0, how important this memory is. Higher = slower decay. Default 0.5.
        tags: Comma-separated tags for episodic memories.
    """
    vec = await embed(text if memory_type != "semantic" else f"{key} {text}")
    conn = get_db()

    try:
        if memory_type == "semantic":
            if not key:
                return "Error: 'key' is required for semantic memories (e.g. 'pref.theme', 'project.active')"
            written = write_semantic(conn, key, text, embedding=vec, source="user_explicit", confidence=1.0)
            if written:
                _maybe_sync()
                return f"Stored semantic memory: {key} = {text}"
            return f"Could not overwrite existing higher-priority memory at '{key}'"

        elif memory_type == "lesson":
            tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
            category = tag_list[0] if tag_list else "preference"
            row_id = write_lesson(conn, text, category=category, embedding=vec)
            _maybe_sync()
            return f"Stored lesson #{row_id}: {text}"

        else:  # episodic
            tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
            row_id = write_episodic(conn, text, embedding=vec, tags=tag_list, importance=importance)
            _maybe_sync()
            return f"Stored episodic memory #{row_id} (importance={importance})"

    finally:
        conn.close()


@mcp.tool(name="Recall")
async def recall(query: str, memory_type: str = "all", limit: int = 10, score_threshold: float = 0.35, category: str = "") -> str:
    """Search memory by semantic similarity.

    Args:
        query: What to search for. Can be a question, topic, or keyword.
        memory_type: One of 'all', 'semantic', 'episodic', or 'lessons'.
        limit: Max results to return. Default 10.
        score_threshold: Minimum similarity score (0.0–1.0) to include a result.
                         Applied to semantic cosine scores and episodic decay scores.
                         Default 0.35. Set to 0.0 to disable filtering.
        category: Filter lessons by category (e.g. 'preference', 'tool', 'knowledge').
                  Only applied when memory_type includes lessons. Empty string returns all.
    """
    vec = await embed(query)
    conn = get_db()

    try:
        sections = []

        if memory_type in ("all", "lessons"):
            lessons = get_lessons(conn, category=category or None)
            if lessons:
                lines = [f"- [{l['category']}] {l['rule']}" for l in lessons]
                header = f"## Lessons ({len(lessons)} total)"
                if category:
                    header += f" [category={category}]"
                sections.append(header + "\n" + "\n".join(lines))

        if memory_type in ("all", "semantic"):
            results = search_semantic(conn, query_vec=vec, limit=limit, score_threshold=score_threshold)
            if results:
                lines = []
                for r in results:
                    score_str = f" (score={r['score']:.2f})" if r["score"] > 0 else ""
                    lines.append(f"- **{r['key']}**: {r['value']}{score_str}")
                sections.append(f"## Semantic Memory ({len(results)} results)\n" + "\n".join(lines))

        if memory_type in ("all", "episodic"):
            results = search_episodic(conn, query_vec=vec, limit=limit, score_threshold=score_threshold)
            if results:
                # Update accessed_at so recalled memories decay more slowly
                update_episodic_accessed(conn, [r["id"] for r in results])
                lines = []
                for r in results:
                    tag_str = f" [{', '.join(r['tags'])}]" if r["tags"] else ""
                    lines.append(f"- {r['text']}{tag_str} (score={r['score']:.3f}, {r['days_old']}d ago)")
                sections.append(f"## Episodic Memory ({len(results)} results)\n" + "\n".join(lines))

        if not sections:
            return "No memories found."

        return "\n\n".join(sections)

    finally:
        conn.close()


@mcp.tool(name="Learn")
async def learn(rule: str, category: str = "preference") -> str:
    """Store a correction or rule that should always be followed.

    These have the highest priority and override other memories.

    Args:
        rule: The rule or correction. e.g. "Always use wikilinks when writing to Obsidian"
        category: One of 'preference', 'tool', 'knowledge'. Default 'preference'.
    """
    vec = await embed(rule)
    conn = get_db()
    try:
        row_id = write_lesson(conn, rule, category=category, embedding=vec)
        _maybe_sync()
        return f"Learned lesson #{row_id} [{category}]: {rule}"
    finally:
        conn.close()


@mcp.tool(name="Forget")
async def forget(memory_type: str, identifier: str) -> str:
    """Remove a memory.

    Args:
        memory_type: One of 'semantic', 'episodic', or 'lesson'.
        identifier: The key (for semantic) or ID number (for episodic/lesson).
    """
    conn = get_db()
    try:
        if memory_type == "semantic":
            if delete_semantic(conn, identifier):
                return f"Deleted semantic memory '{identifier}'"
            return f"No semantic memory found with key '{identifier}'"

        elif memory_type == "episodic":
            if delete_episodic(conn, int(identifier)):
                return f"Deleted episodic memory #{identifier}"
            return f"No episodic memory found with ID {identifier}"

        elif memory_type == "lesson":
            if delete_lesson(conn, int(identifier)):
                return f"Deleted lesson #{identifier}"
            return f"No lesson found with ID {identifier}"

        else:
            return f"Unknown memory type: {memory_type}. Use 'semantic', 'episodic', or 'lesson'."

    finally:
        conn.close()


@mcp.tool(name="MemoryStats")
async def memory_stats() -> str:
    """Show memory statistics — counts, age, and decay health per memory tier."""
    import math
    import time
    from datetime import datetime

    conn = get_db()
    try:
        now = time.time()

        # --- Semantic ---
        sem_rows = conn.execute(
            "SELECT COUNT(*) as cnt, MIN(created_at) as oldest, MAX(updated_at) as newest FROM semantic"
        ).fetchone()
        sem_count = sem_rows["cnt"]
        sem_oldest = datetime.fromtimestamp(sem_rows["oldest"]).strftime("%Y-%m-%d") if sem_rows["oldest"] else None
        sem_newest = datetime.fromtimestamp(sem_rows["newest"]).strftime("%Y-%m-%d") if sem_rows["newest"] else None

        # --- Episodic ---
        epi_active = conn.execute(
            "SELECT id, importance, created_at FROM episodic WHERE is_deleted = 0"
        ).fetchall()
        epi_deleted = conn.execute("SELECT COUNT(*) FROM episodic WHERE is_deleted = 1").fetchone()[0]
        epi_count = len(epi_active)

        near_zero = 0
        oldest_epi_ts = None
        for row in epi_active:
            days_old = (now - row["created_at"]) / 86400
            max_score = 0.3 * 1.0 * math.exp(-0.03 * days_old)
            if max_score < 0.1:
                near_zero += 1
            if oldest_epi_ts is None or row["created_at"] < oldest_epi_ts:
                oldest_epi_ts = row["created_at"]
        oldest_epi = datetime.fromtimestamp(oldest_epi_ts).strftime("%Y-%m-%d") if oldest_epi_ts else None

        # --- Lessons ---
        les_rows = conn.execute(
            "SELECT COUNT(*) as cnt, MIN(created_at) as oldest FROM lessons"
        ).fetchone()
        les_count = les_rows["cnt"]
        les_oldest = datetime.fromtimestamp(les_rows["oldest"]).strftime("%Y-%m-%d") if les_rows["oldest"] else None

        stats = {
            "semantic": {
                "count": sem_count,
                "oldest": sem_oldest,
                "last_updated": sem_newest,
            },
            "episodic": {
                "active": epi_count,
                "soft_deleted": epi_deleted,
                "near_zero_score": near_zero,
                "oldest": oldest_epi,
                "prune_recommended": epi_deleted > 0 or near_zero > 0,
            },
            "lessons": {
                "count": les_count,
                "oldest": les_oldest,
            },
            "total": sem_count + epi_count + les_count,
        }
        return json.dumps(stats, indent=2)
    finally:
        conn.close()


@mcp.tool(name="PruneMemory")
async def prune_memory(score_floor: float = 0.05) -> str:
    """Hard-delete decayed and soft-deleted episodic memories.

    Removes soft-deleted rows and any active episodic rows whose maximum
    possible decay score has fallen below score_floor (~115 days at default).

    Args:
        score_floor: Rows with max possible decay score below this are removed.
                     Default 0.05. Set lower (e.g. 0.01) to be more conservative.
    """
    conn = get_db()
    try:
        result = prune_episodic(conn, score_floor=score_floor)
        total = result["soft_deleted"] + result["decayed"]
        if total == 0:
            return "Nothing to prune — no soft-deleted or sufficiently decayed episodic memories."
        parts = []
        if result["soft_deleted"]:
            parts.append(f"{result['soft_deleted']} soft-deleted")
        if result["decayed"]:
            parts.append(f"{result['decayed']} fully decayed")
        return f"Pruned {total} episodic memories ({', '.join(parts)})."
    finally:
        conn.close()


if __name__ == "__main__":
    mcp.run()

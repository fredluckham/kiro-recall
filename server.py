"""Kiro Memory MCP Server — persistent local memory with semantic recall."""

import json
import sys
from pathlib import Path

# Ensure local modules are importable
sys.path.insert(0, str(Path(__file__).parent))

from mcp.server import MCPServer
from mcp.server.stdio import stdio_server

from db import (
    get_db,
    write_semantic,
    write_episodic,
    write_lesson,
    search_semantic,
    search_episodic,
    get_lessons,
    delete_semantic,
    delete_episodic,
    delete_lesson,
)
from embed import embed

mcp = MCPServer(name="kiro-memory")


@mcp.tool()
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
                return f"Stored semantic memory: {key} = {text}"
            return f"Could not overwrite existing higher-priority memory at '{key}'"

        elif memory_type == "lesson":
            tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
            category = tag_list[0] if tag_list else "preference"
            row_id = write_lesson(conn, text, category=category, embedding=vec)
            return f"Stored lesson #{row_id}: {text}"

        else:  # episodic
            tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
            row_id = write_episodic(conn, text, embedding=vec, tags=tag_list, importance=importance)
            return f"Stored episodic memory #{row_id} (importance={importance})"

    finally:
        conn.close()


@mcp.tool()
async def recall(query: str, memory_type: str = "all", limit: int = 10) -> str:
    """Search memory by semantic similarity.

    Args:
        query: What to search for. Can be a question, topic, or keyword.
        memory_type: One of 'all', 'semantic', 'episodic', or 'lessons'.
        limit: Max results to return. Default 10.
    """
    vec = await embed(query)
    conn = get_db()

    try:
        sections = []

        if memory_type in ("all", "lessons"):
            lessons = get_lessons(conn)
            if lessons:
                lines = [f"- [{l['category']}] {l['rule']}" for l in lessons]
                sections.append(f"## Lessons ({len(lessons)} total)\n" + "\n".join(lines))

        if memory_type in ("all", "semantic"):
            results = search_semantic(conn, query_vec=vec, limit=limit)
            if results:
                lines = []
                for r in results:
                    score_str = f" (score={r['score']:.2f})" if r["score"] > 0 else ""
                    lines.append(f"- **{r['key']}**: {r['value']}{score_str}")
                sections.append(f"## Semantic Memory ({len(results)} results)\n" + "\n".join(lines))

        if memory_type in ("all", "episodic"):
            results = search_episodic(conn, query_vec=vec, limit=limit)
            if results:
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


@mcp.tool()
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
        return f"Learned lesson #{row_id} [{category}]: {rule}"
    finally:
        conn.close()


@mcp.tool()
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


@mcp.tool()
async def memory_stats() -> str:
    """Show memory statistics — how many items in each store."""
    conn = get_db()
    try:
        sem_count = conn.execute("SELECT COUNT(*) FROM semantic").fetchone()[0]
        epi_count = conn.execute("SELECT COUNT(*) FROM episodic WHERE is_deleted = 0").fetchone()[0]
        les_count = conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]
        return json.dumps(
            {"semantic": sem_count, "episodic": epi_count, "lessons": les_count, "total": sem_count + epi_count + les_count},
            indent=2,
        )
    finally:
        conn.close()


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await mcp.run(read_stream, write_stream)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

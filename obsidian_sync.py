"""Obsidian sync — renders memory state to markdown files with wikilinks."""

import json
import os
import time
from datetime import datetime
from pathlib import Path

from db import get_db, get_lessons

VAULT_ROOT = Path(os.environ.get("KIRO_MEMORY_VAULT", Path.home() / "Documents" / "Obsidian" / "Kiro Knowledge Base"))
MEMORY_DIR = VAULT_ROOT / "Memory"


def sync_all():
    """Sync all memory to Obsidian vault."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    conn = get_db()
    try:
        _sync_semantic(conn)
        _sync_lessons(conn)
        _sync_episodic_today(conn)
    finally:
        conn.close()


def _sync_semantic(conn):
    """Render semantic memory as a structured markdown file."""
    rows = conn.execute(
        "SELECT key, value, source, confidence, updated_at FROM semantic ORDER BY key"
    ).fetchall()

    if not rows:
        return

    lines = ["# Semantic Memory", "", "Auto-synced from kiro-memory. Do not edit directly.", ""]

    current_prefix = ""
    for row in rows:
        prefix = row["key"].split(".")[0] if "." in row["key"] else row["key"]
        if prefix != current_prefix:
            current_prefix = prefix
            lines.append(f"## {prefix.title()}")
            lines.append("")

        updated = datetime.fromtimestamp(row["updated_at"]).strftime("%Y-%m-%d")
        lines.append(f"- **{row['key']}**: {row['value']} _(updated {updated})_")

    lines.append("")
    (MEMORY_DIR / "Semantic.md").write_text("\n".join(lines))


def _sync_lessons(conn):
    """Render lessons as a markdown file."""
    lessons = get_lessons(conn)
    if not lessons:
        return

    lines = [
        "# Learned Corrections",
        "",
        "Rules and corrections that override default behaviour.",
        "",
    ]

    by_category = {}
    for lesson in lessons:
        cat = lesson["category"]
        by_category.setdefault(cat, []).append(lesson)

    for category, items in sorted(by_category.items()):
        lines.append(f"## {category.title()}")
        lines.append("")
        for item in items:
            lines.append(f"- {item['rule']}")
        lines.append("")

    (MEMORY_DIR / "Lessons.md").write_text("\n".join(lines))


def _sync_episodic_today(conn):
    """Append today's episodic memories to the session log."""
    today = datetime.now().strftime("%Y-%m-%d")
    start_of_day = datetime.now().replace(hour=0, minute=0, second=0).timestamp()

    rows = conn.execute(
        "SELECT text, tags, importance, created_at FROM episodic "
        "WHERE is_deleted = 0 AND created_at >= ? ORDER BY created_at",
        (start_of_day,),
    ).fetchall()

    if not rows:
        return

    session_dir = VAULT_ROOT / "Sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    session_file = session_dir / f"{today}.md"

    # Build the memory section
    lines = []
    if not session_file.exists():
        lines.append(f"# Session {today}")
        lines.append("")

    lines.append("## Memory Log")
    lines.append("")

    for row in rows:
        ts = datetime.fromtimestamp(row["created_at"]).strftime("%H:%M")
        tags = json.loads(row["tags"])
        tag_str = " ".join(f"[[{t}]]" for t in tags) if tags else ""
        imp_marker = " ⭐" if row["importance"] >= 0.8 else ""
        lines.append(f"- **{ts}** {row['text']}{imp_marker} {tag_str}".rstrip())

    lines.append("")

    # Append or create
    if session_file.exists():
        content = session_file.read_text()
        if "## Memory Log" in content:
            # Replace the memory log section
            before = content.split("## Memory Log")[0]
            session_file.write_text(before + "\n".join(lines))
        else:
            session_file.write_text(content + "\n" + "\n".join(lines))
    else:
        session_file.write_text("\n".join(lines))


if __name__ == "__main__":
    sync_all()
    print(f"Synced to {VAULT_ROOT}")

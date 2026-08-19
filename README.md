# kiro-memory

A local MCP memory server for [Kiro CLI](https://kiro.dev) that gives your AI assistant persistent, semantic memory across sessions.

Inspired by [Kiro Crew](https://github.com/kirodotdev/KiroCrew)'s memory architecture — reimplemented as a lightweight, self-contained system using SQLite + Ollama embeddings, with Obsidian as the human-readable sync target.

## What it does

- **Semantic memory** — structured key-value facts (`pref.editor: Neovim`, `project.active: MyProject`)
- **Episodic memory** — conversation fragments that decay over time (~23 day half-life)
- **Lessons** — corrections and rules that override all other memory (highest priority)
- **Semantic recall** — vector similarity search via Qwen3-Embedding (1024-dim, runs locally)
- **Obsidian sync** — renders memory to markdown with `[[wikilinks]]` for graph navigation

## Architecture

```
┌──────────────────────────────────────────┐
│  Kiro CLI / any MCP client               │
│  Tools: remember, recall, learn, forget  │
└──────────────┬───────────────────────────┘
               │ stdio (MCP protocol)
       ┌───────▼───────┐          ┌─────────────────┐
       │  memory.db    │  sync →  │  Obsidian Vault  │
       │  (SQLite)     │          │  (Markdown)      │
       └───────┬───────┘          └─────────────────┘
               │
       ┌───────▼───────┐
       │  Ollama       │
       │  qwen3-embed  │
       │  (localhost)   │
       └───────────────┘
```

## Requirements

- macOS or Linux
- Python 3.10+
- [Ollama](https://ollama.com) (for local embeddings)
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

## Install

```bash
git clone https://github.com/YOUR_USERNAME/kiro-memory.git
cd kiro-memory
bash install.sh
```

The install script will:
1. Install Ollama (if not present) and start it as a service
2. Pull the `qwen3-embedding:0.6b` model
3. Create a Python venv and install dependencies
4. Install the MCP server config into `~/.kiro/settings/mcp.json`
5. Install the steering file to `~/.kiro/steering/obsidian-memory.md`
6. Optionally seed memory from an existing Obsidian vault

## Manual setup

If you prefer not to use the install script:

```bash
# 1. Install Ollama and the embedding model
brew install ollama  # or: curl -fsSL https://ollama.com/install.sh | sh
brew services start ollama
ollama pull qwen3-embedding:0.6b

# 2. Create venv and install deps
uv venv .venv
uv pip install "mcp[cli]>=1.0.0" "httpx>=0.27.0"

# 3. Copy to ~/.kiro/memory
mkdir -p ~/.kiro/memory
cp server.py db.py embed.py obsidian_sync.py ~/.kiro/memory/

# 4. Add to MCP config (see install.sh for the JSON patch)
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `remember` | Store a fact, episode, or lesson |
| `recall` | Semantic search across all memory |
| `learn` | Store a high-priority correction/rule |
| `forget` | Remove a memory by key or ID |
| `memory_stats` | Show counts per memory tier |

## Memory tiers

| Tier | Priority | Decay | Use case |
|------|----------|-------|----------|
| Lessons | Highest | None | "Always use wikilinks", "Never assume region" |
| Semantic | High | None (updated in place) | Structured facts about user/projects |
| Episodic | Medium | exp(-0.03 × days) | Conversation fragments, decisions |

## Key format

- `pref.*` — User preferences (`pref.theme`, `pref.voice`, `pref.editor`)
- `project.*` — Active projects (`project.active`, `project.stack`)
- `user.*` — User facts (`user.role`, `user.company`, `user.tools`)

## Obsidian sync

Run manually or via cron:

```bash
~/.kiro/memory/.venv/bin/python ~/.kiro/memory/obsidian_sync.py
```

Outputs:
- `Memory/Semantic.md` — all facts grouped by prefix
- `Memory/Lessons.md` — corrections grouped by category
- `Sessions/YYYY-MM-DD.md` — today's episodic memories

## Configuration

The Obsidian vault path is set in `obsidian_sync.py`:

```python
VAULT_ROOT = Path.home() / "Documents" / "Obsidian" / "Kiro Knowledge Base"
```

The Ollama endpoint is set in `embed.py`:

```python
OLLAMA_URL = "http://localhost:11434/api/embed"
MODEL = "qwen3-embedding:0.6b"
```

## License

MIT

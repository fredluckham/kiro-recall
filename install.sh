#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# kiro-recall installer
# Sets up Ollama, the embedding model, Python deps, and kiro-cli config.
#
# Usage:
#   bash install.sh
#
# Options:
#   --no-seed     Skip seeding memory from existing Obsidian vault
#   --no-ollama   Skip Ollama installation (assumes already installed)
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Parse arguments ──
SKIP_SEED=0
SKIP_OLLAMA=0
for arg in "$@"; do
    case "$arg" in
        --no-seed) SKIP_SEED=1 ;;
        --no-ollama) SKIP_OLLAMA=1 ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

# ── Colors ──
if [ -t 1 ] && command -v tput >/dev/null 2>&1; then
    BOLD=$(tput bold); RESET=$(tput sgr0)
    GREEN=$(tput setaf 2); CYAN=$(tput setaf 6); RED=$(tput setaf 1); DIM=$(tput dim)
else
    BOLD="" RESET="" GREEN="" CYAN="" RED="" DIM=""
fi

ok() { echo "  ${GREEN}✓${RESET} $1"; }
info() { echo "  ${DIM}→${RESET} $1"; }
fail() { echo "  ${RED}✗${RESET} $1"; exit 1; }
step() { echo ""; echo "  ${CYAN}${BOLD}[$1]${RESET} ${BOLD}$2${RESET}"; }

# ── Constants ──
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="$HOME/.kiro/recall"
MCP_CONFIG="$HOME/.kiro/settings/mcp.json"
STEERING_DIR="$HOME/.kiro/steering"
EMBEDDING_MODEL="qwen3-embedding:0.6b"

has() { command -v "$1" >/dev/null 2>&1; }

echo ""
echo "${BOLD}  kiro-recall installer${RESET}"
echo "  ${DIM}────────────────────────────────────${RESET}"

# ══════════════════════════════════════════════════════════════════════
# Step 1: Ollama
# ══════════════════════════════════════════════════════════════════════
step "1/6" "Ollama"

if [ "$SKIP_OLLAMA" -eq 1 ]; then
    info "Skipping Ollama install (--no-ollama)"
elif has ollama; then
    ok "Ollama already installed ($(ollama --version 2>/dev/null || echo 'unknown version'))"
else
    info "Installing Ollama..."
    if [ "$(uname)" = "Darwin" ]; then
        if has brew; then
            brew install ollama >/dev/null 2>&1
            ok "Installed via Homebrew"
        else
            curl -fsSL https://ollama.com/install.sh | sh
            ok "Installed via official script"
        fi
    else
        curl -fsSL https://ollama.com/install.sh | sh
        ok "Installed via official script"
    fi
fi

# Start Ollama service
if [ "$(uname)" = "Darwin" ] && has brew; then
    if ! brew services list 2>/dev/null | grep -q "ollama.*started"; then
        brew services start ollama >/dev/null 2>&1 || true
        sleep 2
    fi
    ok "Ollama service running"
elif has systemctl; then
    if ! systemctl is-active --quiet ollama 2>/dev/null; then
        sudo systemctl start ollama 2>/dev/null || ollama serve &>/dev/null &
        sleep 2
    fi
    ok "Ollama service running"
else
    # Fallback: start in background if not running
    if ! curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
        ollama serve &>/dev/null &
        sleep 3
    fi
    ok "Ollama running"
fi

# Pull embedding model
if ollama list 2>/dev/null | grep -q "$EMBEDDING_MODEL"; then
    ok "Model $EMBEDDING_MODEL already pulled"
else
    info "Pulling $EMBEDDING_MODEL (~639MB)..."
    ollama pull "$EMBEDDING_MODEL"
    ok "Model ready"
fi

# ══════════════════════════════════════════════════════════════════════
# Step 2: Python environment
# ══════════════════════════════════════════════════════════════════════
step "2/6" "Python environment"

mkdir -p "$INSTALL_DIR"

# Copy source files
cp "$SCRIPT_DIR/server.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/db.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/embed.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/obsidian_sync.py" "$INSTALL_DIR/"
ok "Source files copied to $INSTALL_DIR"

# Create venv
if has uv; then
    (cd "$INSTALL_DIR" && uv venv .venv >/dev/null 2>&1)
    (cd "$INSTALL_DIR" && uv pip install "mcp[cli]>=1.0.0" "httpx>=0.27.0" >/dev/null 2>&1)
    ok "Dependencies installed via uv"
elif has python3; then
    python3 -m venv "$INSTALL_DIR/.venv"
    "$INSTALL_DIR/.venv/bin/pip" install --upgrade pip >/dev/null 2>&1
    "$INSTALL_DIR/.venv/bin/pip" install "mcp[cli]>=1.0.0" "httpx>=0.27.0" >/dev/null 2>&1
    ok "Dependencies installed via pip"
else
    fail "Python 3.10+ required but not found"
fi

# Verify
"$INSTALL_DIR/.venv/bin/python" -c "import mcp, httpx" || fail "Import check failed"
ok "Python environment verified"

# ══════════════════════════════════════════════════════════════════════
# Step 3: Configuration
# ══════════════════════════════════════════════════════════════════════
step "3/6" "Configuration"

PYTHON_PATH="$INSTALL_DIR/.venv/bin/python"
SERVER_PATH="$INSTALL_DIR/server.py"

# Prompt for vault path (needed for MCP config below)
DEFAULT_VAULT="$HOME/Documents/Obsidian/Kiro Knowledge Base"
printf "  ${DIM}→${RESET} Obsidian vault path [${DIM}%s${RESET}]: " "$DEFAULT_VAULT"
read -r VAULT_PATH < /dev/tty
VAULT_PATH="${VAULT_PATH:-$DEFAULT_VAULT}"

# Prompt for autosync
printf "  ${DIM}→${RESET} Enable auto Obsidian sync after every Remember/Learn call? [y/N]: "
read -r AUTOSYNC_ANSWER < /dev/tty
AUTOSYNC_VAL="0"
case "$AUTOSYNC_ANSWER" in
    [Yy]*) AUTOSYNC_VAL="1" ;;
esac

# Write env file for the server and sync script
cat > "$INSTALL_DIR/.env" <<EOF
KIRO_MEMORY_VAULT=$VAULT_PATH
KIRO_MEMORY_AUTOSYNC=$AUTOSYNC_VAL
EOF
ok "Configuration saved to $INSTALL_DIR/.env"

mkdir -p "$(dirname "$MCP_CONFIG")"

if [ -f "$MCP_CONFIG" ]; then
    if grep -q '"kiro-recall"' "$MCP_CONFIG"; then
        ok "kiro-recall already in MCP config"
    else
        "$PYTHON_PATH" -c "
import json
with open('$MCP_CONFIG', 'r') as f:
    config = json.load(f)
config.setdefault('mcpServers', {})
config['mcpServers']['kiro-recall'] = {
    'command': '$PYTHON_PATH',
    'args': ['$SERVER_PATH'],
    'disabled': False,
    'autoApprove': ['Remember', 'Recall', 'Learn', 'Forget', 'MemoryStats', 'PruneMemory'],
    'env': {'KIRO_MEMORY_VAULT': '$VAULT_PATH'}
}
with open('$MCP_CONFIG', 'w') as f:
    json.dump(config, f, indent=2)
"
        ok "Added kiro-recall to $MCP_CONFIG"
    fi
else
    cat > "$MCP_CONFIG" <<EOF
{
  "mcpServers": {
    "kiro-recall": {
      "command": "$PYTHON_PATH",
      "args": ["$SERVER_PATH"],
      "disabled": false,
      "autoApprove": ["Remember", "Recall", "Learn", "Forget", "MemoryStats", "PruneMemory"],
      "env": {"KIRO_MEMORY_VAULT": "$VAULT_PATH"}
    }
  }
}
EOF
    ok "Created $MCP_CONFIG with kiro-recall"
fi

# ══════════════════════════════════════════════════════════════════════
# Step 4: Steering file
# ══════════════════════════════════════════════════════════════════════
step "4/6" "Steering file"

mkdir -p "$STEERING_DIR"
STEERING_FILE="$STEERING_DIR/obsidian-memory.md"

if [ -f "$STEERING_FILE" ]; then
    ok "Steering file already exists"
else
    cat > "$STEERING_FILE" << 'STEERING'
# Kiro Memory System

You have persistent local memory via the `kiro-recall` MCP server. Use it proactively.

## On Session Start

Call `Recall` with a broad query about the user and current context to load relevant memory:
- `Recall("user preferences and current projects")`
- If the conversation has a clear topic, also `Recall("topic keywords here")`

## During Conversations

### When to `Remember`
- User states a preference or correction → `Remember` as semantic (`pref.*` key)
- User mentions a project, tool, or workflow → `Remember` as semantic (`project.*` or `user.*`)
- A significant decision, insight, or outcome occurs → `Remember` as episodic with appropriate importance
- User explicitly says "remember this" → `Remember` with importance=0.9

### When to `Learn`
- User corrects your behaviour → `Learn` the correction as a rule
- User says "always do X" or "never do Y" → `Learn` it
- A pattern emerges where you keep getting something wrong → `Learn` the fix

### When to `Recall`
- Before making assumptions about the user's setup, preferences, or projects
- When the user references something from a previous session
- When you need context about a topic you've discussed before

## Key Format for Semantic Memory
- `pref.*` — User preferences (pref.theme, pref.voice, pref.editor)
- `project.*` — Active projects (project.active, project.customersolutions)
- `user.*` — User facts (user.role, user.company, user.tools)

## Obsidian Vault

The user's knowledge base path is configured via `KIRO_MEMORY_VAULT` env var.
- Always use `[[wikilink]]` style when writing to the vault
- Memory syncs to `Memory/Semantic.md`, `Memory/Lessons.md`, and session logs
- The vault also has `Topics/`, `Profile.md`, and `Sessions/` for reference

## Priority (highest to lowest)
1. Lessons (corrections) — always override
2. Semantic memory (user-explicit) — structured facts
3. Episodic memory — decaying conversation context
STEERING
    ok "Installed steering file"
fi

# ══════════════════════════════════════════════════════════════════════
# Step 5: Verify
# ══════════════════════════════════════════════════════════════════════
# Step 5: Seed from Obsidian (optional)
# ══════════════════════════════════════════════════════════════════════
step "5/6" "Seed memory from Obsidian"

if [ "$SKIP_SEED" -eq 1 ]; then
    info "Skipping seed (--no-seed)"
elif [ ! -d "$VAULT_PATH" ]; then
    info "Vault not found at $VAULT_PATH — skipping seed"
else
    printf "  ${DIM}→${RESET} Seed memory from your Obsidian vault? [y/N]: "
    read -r SEED_ANSWER < /dev/tty
    case "$SEED_ANSWER" in
        [Yy]*)
            info "Looking for markdown files to seed from..."
            SEEDED=0
            for f in "$VAULT_PATH/About Me.md" "$VAULT_PATH/Profile.md" "$VAULT_PATH/Projects.md" "$VAULT_PATH/Kiro Notes.md"; do
                if [ -f "$f" ]; then
                    "$PYTHON_PATH" -c "
import sys, os
sys.path.insert(0, '$INSTALL_DIR')
os.environ['KIRO_MEMORY_VAULT'] = '$VAULT_PATH'
import asyncio
from server import remember
result = asyncio.run(remember(
    text=open('$f').read()[:2000],
    memory_type='episodic',
    importance=0.8,
    tags='seed,obsidian'
))
print(result)
" && info "Seeded: $(basename "$f")" && SEEDED=$((SEEDED + 1))
                fi
            done
            if [ "$SEEDED" -eq 0 ]; then
                info "No standard seed files found (About Me.md, Profile.md, Projects.md, Kiro Notes.md)"
            else
                ok "Seeded $SEEDED file(s) from vault"
            fi
            ;;
        *)
            info "Skipping seed"
            ;;
    esac
fi

# ══════════════════════════════════════════════════════════════════════
# Step 6: Verify
# ══════════════════════════════════════════════════════════════════════
step "6/6" "Verify"

# Test embedding endpoint
if curl -s http://localhost:11434/api/embed -d "{\"model\":\"$EMBEDDING_MODEL\",\"input\":\"test\"}" | grep -q "embeddings"; then
    ok "Ollama embedding endpoint working"
else
    fail "Cannot reach Ollama embedding endpoint at localhost:11434"
fi

# Test server loads
"$PYTHON_PATH" -c "
import sys
sys.path.insert(0, '$INSTALL_DIR')
from server import mcp
import asyncio
tools = asyncio.run(mcp.list_tools())
assert len(tools) == 6, f'Expected 6 tools, got {len(tools)}'
" && ok "MCP server loads with 6 tools" || fail "Server failed to load"

# ── Done ──
echo ""
echo "  ${GREEN}${BOLD}✓ kiro-recall installed successfully${RESET}"
echo ""
echo "  ${DIM}────────────────────────────────────${RESET}"
echo ""
echo "  ${BOLD}Next steps:${RESET}"
echo "    1. Restart your kiro-cli session to pick up the new MCP server"
echo "    2. The AI will now have access to: Remember, Recall, Learn, Forget"
echo ""
echo "  ${BOLD}Obsidian sync:${RESET}"
echo "    $PYTHON_PATH $INSTALL_DIR/obsidian_sync.py"
echo ""
echo "  ${BOLD}Memory database:${RESET}"
echo "    $INSTALL_DIR/memory.db"
echo ""

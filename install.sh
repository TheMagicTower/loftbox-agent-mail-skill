#!/bin/sh
set -eu

SKILL_NAMES="register-loftbox-mail-agent send-loftbox-mail check-loftbox-mail"
ARCHIVE_URL="${LOFTBOX_SKILL_ARCHIVE_URL:-https://github.com/TheMagicTower/loftbox-agent-mail-skill/archive/refs/heads/main.tar.gz}"
AGENT="${LOFTBOX_AGENT:-auto}"
TARGET="${LOFTBOX_SKILLS_DIR:-${AGENT_SKILLS_DIR:-}}"

usage() {
    cat <<'EOF'
Install the LoftBox mail agent skills.

Usage:
  curl -fsSL https://loftbox.net/install.sh | sh
  curl -fsSL https://loftbox.net/install.sh | sh -s -- --agent claude
  curl -fsSL https://loftbox.net/install.sh | sh -s -- --target "$HOME/.my-agent/skills"

Options:
  --agent auto|codex|claude|opencode|cursor|windsurf|aider|openclaw|generic
  --target DIR   Install into an explicit skills directory.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --agent)
            [ "$#" -ge 2 ] || { echo "Missing value for --agent" >&2; exit 2; }
            AGENT="$2"
            shift 2
            ;;
        --target)
            [ "$#" -ge 2 ] || { echo "Missing value for --target" >&2; exit 2; }
            TARGET="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

detect_agent() {
    if [ "$AGENT" != "auto" ]; then
        printf '%s\n' "$AGENT"
        return
    fi
    if [ -n "${CODEX_HOME:-}" ]; then
        printf '%s\n' "codex"
    elif [ -d "${HOME:-}/.claude" ] && [ ! -d "${HOME:-}/.codex" ]; then
        printf '%s\n' "claude"
    elif [ -d "${HOME:-}/.codex" ]; then
        printf '%s\n' "codex"
    elif [ -d "${HOME:-}/.opencode" ]; then
        printf '%s\n' "opencode"
    elif [ -d "${HOME:-}/.cursor" ]; then
        printf '%s\n' "cursor"
    elif [ -d "${HOME:-}/.windsurf" ]; then
        printf '%s\n' "windsurf"
    elif [ -d "${HOME:-}/.aider" ]; then
        printf '%s\n' "aider"
    elif [ -d "${HOME:-}/.openclaw" ]; then
        printf '%s\n' "openclaw"
    else
        printf '%s\n' "generic"
    fi
}

skills_dir_for_agent() {
    agent="$1"
    case "$agent" in
        codex)
            printf '%s\n' "${CODEX_HOME:-$HOME/.codex}/skills"
            ;;
        claude)
            printf '%s\n' "${CLAUDE_HOME:-$HOME/.claude}/skills"
            ;;
        opencode)
            printf '%s\n' "${OPENCODE_HOME:-$HOME/.opencode}/skills"
            ;;
        cursor)
            printf '%s\n' "${CURSOR_HOME:-$HOME/.cursor}/skills"
            ;;
        windsurf)
            printf '%s\n' "${WINDSURF_HOME:-$HOME/.windsurf}/skills"
            ;;
        aider)
            printf '%s\n' "${AIDER_HOME:-$HOME/.aider}/skills"
            ;;
        openclaw)
            printf '%s\n' "${OPENCLAW_HOME:-$HOME/.openclaw}/skills"
            ;;
        generic)
            printf '%s\n' "${HOME:-.}/.agent/skills"
            ;;
        *)
            echo "Unsupported agent: $agent" >&2
            echo "Use --target DIR for a custom agent." >&2
            exit 2
            ;;
    esac
}

download() {
    url="$1"
    out="$2"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$url" -o "$out"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$out" "$url"
    else
        echo "curl or wget is required." >&2
        exit 1
    fi
}

command -v tar >/dev/null 2>&1 || { echo "tar is required." >&2; exit 1; }

AGENT="$(detect_agent)"
if [ -z "$TARGET" ]; then
    TARGET="$(skills_dir_for_agent "$AGENT")"
fi

TMPDIR="$(mktemp -d 2>/dev/null || mktemp -d -t loftbox-skill)"
cleanup() {
    rm -rf "$TMPDIR"
}
trap cleanup EXIT INT TERM

ARCHIVE="$TMPDIR/skill.tar.gz"
download "$ARCHIVE_URL" "$ARCHIVE"
tar -xzf "$ARCHIVE" -C "$TMPDIR"

INSTALLED=""
for skill_name in $SKILL_NAMES; do
    SRC="$TMPDIR/loftbox-agent-mail-skill-main/$skill_name"
    DEST="$TARGET/$skill_name"

    [ -f "$SRC/SKILL.md" ] || { echo "Skill archive did not contain $skill_name/SKILL.md" >&2; exit 1; }

    mkdir -p "$DEST"
    (cd "$SRC" && tar -cf - .) | (cd "$DEST" && tar -xf -)
    INSTALLED="${INSTALLED}
  $DEST"
done

cat <<EOF
Installed LoftBox skills:$INSTALLED

Use this prompt with your agent:
  If the LoftBox mail skill is missing, install it with:
  curl -fsSL https://loftbox.net/install.sh | sh

  Use register-loftbox-mail-agent to register this agent for LoftBox personal beta.
  Ask me only for my owner email.
  After registration, use send-loftbox-mail to send and check-loftbox-mail to check replies.
EOF

#!/bin/sh
set -eu

SKILL_NAMES="register-loftbox-mail-agent send-loftbox-mail check-loftbox-mail"
DEFAULT_ARCHIVE_URL="https://github.com/TheMagicTower/loftbox-agent-mail-skill/archive/refs/heads/main.tar.gz"
ARCHIVE_URL="${LOFTBOX_SKILL_ARCHIVE_URL:-}"
VERSION_URL="${LOFTBOX_SKILL_VERSION_URL:-https://loftbox.net/skill-version.json}"
VERSION_FILE=".loftbox-skill-version"
ALLOW_UNTRUSTED_ARCHIVE="${LOFTBOX_ALLOW_UNTRUSTED_ARCHIVE:-0}"
AGENT="${LOFTBOX_AGENT:-auto}"
TARGET="${LOFTBOX_SKILLS_DIR:-${AGENT_SKILLS_DIR:-}}"
BIN_DIR="${LOFTBOX_BIN_DIR:-${AGENT_BIN_DIR:-}}"
INSTALL_BIN="${LOFTBOX_INSTALL_BIN:-1}"
MODE="install"

usage() {
    cat <<'EOF'
Install the LoftBox mail agent skills.

Usage:
  curl -fsSL https://loftbox.net/install.sh | sh
  curl -fsSL https://loftbox.net/install.sh | sh -s -- --agent claude
  curl -fsSL https://loftbox.net/install.sh | sh -s -- --target "$HOME/.my-agent/skills"
  curl -fsSL https://loftbox.net/install.sh | sh -s -- --bin-dir "$HOME/.local/bin"
  curl -fsSL https://loftbox.net/install.sh | sh -s -- --check
  curl -fsSL https://loftbox.net/install.sh | sh -s -- --update

Options:
  --agent auto|codex|claude|opencode|cursor|windsurf|aider|openclaw|generic
  --target DIR   Install into an explicit skills directory.
  --bin-dir DIR  Install send/check command shims into DIR.
  --no-bin       Do not install command shims.
  --check        Check whether a newer LoftBox skill bundle is available.
  --update       Install the latest published LoftBox skill bundle.
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
        --bin-dir)
            [ "$#" -ge 2 ] || { echo "Missing value for --bin-dir" >&2; exit 2; }
            BIN_DIR="$2"
            INSTALL_BIN="1"
            shift 2
            ;;
        --no-bin)
            INSTALL_BIN="0"
            shift
            ;;
        --check)
            MODE="check"
            shift
            ;;
        --update)
            MODE="update"
            shift
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

default_bin_dir() {
    printf '%s\n' "${HOME:-.}/.local/bin"
}

shell_quote() {
    printf "'"
    printf '%s' "$1" | sed "s/'/'\\\\''/g"
    printf "'"
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

json_value() {
    key="$1"
    file="$2"
    sed -n "s/.*\"$key\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" "$file" | head -n 1
}

read_installed_field() {
    key="$1"
    file="$TARGET/$VERSION_FILE"
    if [ -f "$file" ]; then
        json_value "$key" "$file"
    fi
}

is_trusted_archive_url() {
    case "$1" in
        https://github.com/TheMagicTower/loftbox-agent-mail-skill/archive/*)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

require_trusted_archive_url() {
    url="$1"
    source="$2"
    if is_trusted_archive_url "$url"; then
        return 0
    fi
    if [ "$source" = "override" ] && [ "$ALLOW_UNTRUSTED_ARCHIVE" = "1" ]; then
        echo "Warning: using untrusted LoftBox skill archive URL from LOFTBOX_SKILL_ARCHIVE_URL." >&2
        return 0
    fi
    echo "Untrusted LoftBox skill archive URL from $source: $url" >&2
    echo "Expected https://github.com/TheMagicTower/loftbox-agent-mail-skill/archive/..." >&2
    if [ "$source" = "override" ]; then
        echo "For local tests only, set LOFTBOX_ALLOW_UNTRUSTED_ARCHIVE=1." >&2
    fi
    exit 1
}

AGENT="$(detect_agent)"
if [ -z "$TARGET" ]; then
    TARGET="$(skills_dir_for_agent "$AGENT")"
fi

TMPDIR="$(mktemp -d 2>/dev/null || mktemp -d -t loftbox-skill)"
cleanup() {
    rm -rf "$TMPDIR"
}
trap cleanup EXIT INT TERM

REMOTE_VERSION_FILE="$TMPDIR/skill-version.json"
REMOTE_VERSION=""
REMOTE_COMMIT=""
REMOTE_ARCHIVE_URL=""
if download "$VERSION_URL" "$REMOTE_VERSION_FILE" 2>/dev/null; then
    REMOTE_VERSION="$(json_value version "$REMOTE_VERSION_FILE")"
    REMOTE_COMMIT="$(json_value commit "$REMOTE_VERSION_FILE")"
    REMOTE_ARCHIVE_URL="$(json_value archive_url "$REMOTE_VERSION_FILE")"
fi

if [ "$MODE" = "check" ]; then
    INSTALLED_VERSION="$(read_installed_field version || true)"
    INSTALLED_COMMIT="$(read_installed_field commit || true)"

    if [ -z "$REMOTE_VERSION" ] && [ -z "$REMOTE_COMMIT" ]; then
        echo "Could not fetch LoftBox skill version metadata from $VERSION_URL." >&2
        exit 1
    fi
    if [ -z "$REMOTE_ARCHIVE_URL" ]; then
        echo "LoftBox skill version metadata did not include archive_url." >&2
        exit 1
    fi
    require_trusted_archive_url "$REMOTE_ARCHIVE_URL" "version metadata"

    echo "LoftBox skill bundle update check"
    echo "  installed version: ${INSTALLED_VERSION:-unknown}"
    echo "  installed commit:  ${INSTALLED_COMMIT:-unknown}"
    echo "  latest version:    ${REMOTE_VERSION:-unknown}"
    echo "  latest commit:     ${REMOTE_COMMIT:-unknown}"

    if [ -n "$INSTALLED_COMMIT" ] && [ -n "$REMOTE_COMMIT" ]; then
        if [ "$INSTALLED_COMMIT" = "$REMOTE_COMMIT" ]; then
            echo "Status: up to date"
        else
            echo "Status: update available"
            echo "Run after operator approval:"
            echo "  curl -fsSL https://loftbox.net/install.sh | sh -s -- --update"
        fi
    elif [ -n "$INSTALLED_VERSION" ] && [ -n "$REMOTE_VERSION" ] && [ "$INSTALLED_VERSION" = "$REMOTE_VERSION" ]; then
        echo "Status: up to date"
    else
        echo "Status: update available"
        echo "Run after operator approval:"
        echo "  curl -fsSL https://loftbox.net/install.sh | sh -s -- --update"
    fi
    exit 0
fi

command -v tar >/dev/null 2>&1 || { echo "tar is required." >&2; exit 1; }

if [ -z "$ARCHIVE_URL" ]; then
    if [ "$MODE" = "update" ] && { [ -z "$REMOTE_COMMIT" ] || [ -z "$REMOTE_ARCHIVE_URL" ]; }; then
        echo "Could not fetch valid LoftBox skill update metadata from $VERSION_URL." >&2
        exit 1
    fi
    ARCHIVE_URL="${REMOTE_ARCHIVE_URL:-$DEFAULT_ARCHIVE_URL}"
    require_trusted_archive_url "$ARCHIVE_URL" "version metadata"
else
    require_trusted_archive_url "$ARCHIVE_URL" "override"
fi

ARCHIVE="$TMPDIR/skill.tar.gz"
download "$ARCHIVE_URL" "$ARCHIVE"
tar -xzf "$ARCHIVE" -C "$TMPDIR"

FIRST_SKILL="$(printf '%s\n' $SKILL_NAMES | head -n 1)"
ARCHIVE_ROOT="$(find "$TMPDIR" -type f -path "*/$FIRST_SKILL/SKILL.md" | sed "s#/$FIRST_SKILL/SKILL.md##" | head -n 1)"
[ -n "$ARCHIVE_ROOT" ] || { echo "Skill archive did not contain $FIRST_SKILL/SKILL.md" >&2; exit 1; }

INSTALLED=""
for skill_name in $SKILL_NAMES; do
    SRC="$ARCHIVE_ROOT/$skill_name"
    DEST="$TARGET/$skill_name"

    [ -f "$SRC/SKILL.md" ] || { echo "Skill archive did not contain $skill_name/SKILL.md" >&2; exit 1; }

    rm -rf "$DEST"
    mkdir -p "$DEST"
    (cd "$SRC" && tar -cf - .) | (cd "$DEST" && tar -xf -)
    INSTALLED="${INSTALLED}
  $DEST"
done

mkdir -p "$TARGET"
cat > "$TARGET/$VERSION_FILE" <<EOF
{
  "version": "${REMOTE_VERSION:-unknown}",
  "commit": "${REMOTE_COMMIT:-unknown}",
  "archive_url": "$ARCHIVE_URL",
  "installed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || printf unknown)"
}
EOF

INSTALLED_BIN=""
if [ "$INSTALL_BIN" = "1" ]; then
    if [ -z "$BIN_DIR" ]; then
        BIN_DIR="$(default_bin_dir)"
    fi
    mkdir -p "$BIN_DIR"

    SEND_SKILL_DIR="$(shell_quote "$TARGET/send-loftbox-mail")"
    CHECK_SKILL_DIR="$(shell_quote "$TARGET/check-loftbox-mail")"

    cat > "$BIN_DIR/send-loftbox-mail" <<EOF
#!/bin/sh
set -eu
command -v python3 >/dev/null 2>&1 || { echo "python3 is required for send-loftbox-mail." >&2; exit 127; }
SKILL_DIR=$SEND_SKILL_DIR
export SKILL_DIR
exec python3 "\$SKILL_DIR/scripts/send_loftbox_mail.py" "\$@"
EOF
    chmod 755 "$BIN_DIR/send-loftbox-mail"

    cat > "$BIN_DIR/check-loftbox-mail" <<EOF
#!/bin/sh
set -eu
command -v python3 >/dev/null 2>&1 || { echo "python3 is required for check-loftbox-mail." >&2; exit 127; }
SKILL_DIR=$CHECK_SKILL_DIR
export SKILL_DIR
exec python3 "\$SKILL_DIR/scripts/check_loftbox_mail.py" "\$@"
EOF
    chmod 755 "$BIN_DIR/check-loftbox-mail"

    INSTALLED_BIN="
  $BIN_DIR/send-loftbox-mail
  $BIN_DIR/check-loftbox-mail"
fi

cat <<EOF
Installed LoftBox skills:$INSTALLED
Installed LoftBox commands:$INSTALLED_BIN
Version: ${REMOTE_VERSION:-unknown}
Commit: ${REMOTE_COMMIT:-unknown}

If the commands are not on PATH, add:
  export PATH="$BIN_DIR:\$PATH"

Use this prompt with your agent:
  If the LoftBox mail skill is missing, install it with:
  curl -fsSL https://loftbox.net/install.sh | sh

  Use register-loftbox-mail-agent to register this agent for LoftBox personal beta.
  Ask me only for my owner email.
  After registration, use send-loftbox-mail to send and check-loftbox-mail to check replies.
EOF

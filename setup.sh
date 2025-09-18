#!/usr/bin/env bash
set -euo pipefail

# Podcast Insights Interactive Setup Script
# Handles both automatic and manual installation methods

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
ROOT_DIR="$SCRIPT_DIR"

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_color() {
    local color=$1
    shift
    echo -e "${color}$*${NC}"
}

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to find Python 3.10+
find_python() {
    local candidates=(python3.13 python3.12 python3.11 python3.10 python3 python)
    for py in "${candidates[@]}"; do
        if command_exists "$py"; then
            if "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
                echo "$py"
                return 0
            fi
        fi
    done
    return 1
}

# Function to prompt user for input
prompt_user() {
    local prompt="$1"
    local var_name="$2"
    local default="${3:-}"

    if [[ -n "$default" ]]; then
        read -r -p "$prompt [$default]: " value
        value="${value:-$default}"
    else
        read -r -p "$prompt: " value
    fi

    eval "$var_name='$value'"
}

# Function to prompt for secure input (API keys)
prompt_secure() {
    local prompt="$1"
    local var_name="$2"

    echo -n "$prompt: "
    read -r -s value
    echo
    eval "$var_name='$value'"
}

# Function to prompt for choice
prompt_choice() {
    local prompt="$1"
    local options="$2"
    local var_name="$3"
    local default="${4:-}"

    echo "$prompt"
    echo "$options"

    if [[ -n "$default" ]]; then
        read -r -p "Enter choice [$default]: " choice
        choice="${choice:-$default}"
    else
        read -r -p "Enter choice: " choice
    fi

    eval "$var_name='$choice'"
}

# Main setup begins
clear
print_color "$BLUE" "========================================="
print_color "$BLUE" "   Podcast Insights Setup Wizard"
print_color "$BLUE" "========================================="
echo

# Check Python version
print_color "$YELLOW" "Checking Python version..."
PY_BIN="$(find_python || true)"
if [[ -z "${PY_BIN:-}" ]]; then
    print_color "$RED" "Error: Python 3.10+ not found. Please install Python >=3.10 first."
    exit 1
fi
print_color "$GREEN" "✓ Found Python: $PY_BIN"
echo

# Check git availability
if ! command_exists git; then
    print_color "$RED" "Error: git is not installed. Please install git first."
    exit 1
fi
print_color "$GREEN" "✓ Git is installed"

# Check ffmpeg availability (required for transcription)
if ! command_exists ffmpeg; then
    print_color "$RED" "Error: ffmpeg is not installed. This is required for audio transcription."
    print_color "$YELLOW" "Please install ffmpeg first:"
    print_color "$YELLOW" "  macOS: brew install ffmpeg"
    print_color "$YELLOW" "  Ubuntu/Debian: sudo apt-get install ffmpeg"
    print_color "$YELLOW" "  Fedora: sudo dnf install ffmpeg"
    exit 1
fi
print_color "$GREEN" "✓ ffmpeg is installed"
echo

# Choose installation method
print_color "$YELLOW" "Choose installation method:"
prompt_choice "" "  1) Automatic (recommended) - Install bundled tools
  2) Manual - Use existing tool installations" "INSTALL_METHOD" "1"

echo

if [[ "$INSTALL_METHOD" == "1" ]]; then
    # Automatic installation
    print_color "$BLUE" "Starting automatic installation..."
    echo

    # Initialize/update submodules
    print_color "$YELLOW" "Initializing git submodules..."
    if [[ ! -d "$ROOT_DIR/tools/ai-cli" ]] || [[ ! -d "$ROOT_DIR/tools/podcast-transcription" ]]; then
        git submodule update --init --recursive
    else
        print_color "$GREEN" "✓ Submodules already initialized"
    fi
    echo

    # Set up virtualenvs for tools
    print_color "$YELLOW" "Setting up virtual environments for tools..."

    # ai-cli virtualenv
    if [[ ! -d "$ROOT_DIR/tools/ai-cli/.venv" ]]; then
        print_color "$YELLOW" "Creating virtualenv for ai-cli..."
        "$PY_BIN" -m venv "$ROOT_DIR/tools/ai-cli/.venv"
        "$ROOT_DIR/tools/ai-cli/.venv/bin/pip" install --upgrade pip >/dev/null 2>&1
        "$ROOT_DIR/tools/ai-cli/.venv/bin/pip" install -r "$ROOT_DIR/tools/ai-cli/requirements.txt" >/dev/null 2>&1
        print_color "$GREEN" "✓ ai-cli virtualenv created"
    else
        print_color "$GREEN" "✓ ai-cli virtualenv exists"
    fi

    # podcast-transcription virtualenv
    if [[ ! -d "$ROOT_DIR/tools/podcast-transcription/venv" ]]; then
        print_color "$YELLOW" "Creating virtualenv for podcast-transcription..."
        "$PY_BIN" -m venv "$ROOT_DIR/tools/podcast-transcription/venv"
        "$ROOT_DIR/tools/podcast-transcription/venv/bin/pip" install --upgrade pip >/dev/null 2>&1
        "$ROOT_DIR/tools/podcast-transcription/venv/bin/pip" install -r "$ROOT_DIR/tools/podcast-transcription/requirements.txt" >/dev/null 2>&1
        print_color "$GREEN" "✓ podcast-transcription virtualenv created"
    else
        print_color "$GREEN" "✓ podcast-transcription virtualenv exists"
    fi

    echo

    # Set tool paths
    TRANSCRIBE_PATH="$ROOT_DIR/tools/podcast-transcription/transcribe.sh"
    AI_PROMPT_PATH="$ROOT_DIR/tools/ai-cli/ai-prompt"

else
    # Manual installation
    print_color "$BLUE" "Manual installation selected"
    echo
    print_color "$YELLOW" "Please provide paths to the installed tools:"

    prompt_user "Path to local-podcast-transcription/transcribe.sh" "TRANSCRIBE_PATH"
    if [[ ! -f "$TRANSCRIBE_PATH" ]]; then
        print_color "$RED" "Warning: transcribe.sh not found at $TRANSCRIBE_PATH"
    fi

    prompt_user "Path to ai-cli/ai-prompt" "AI_PROMPT_PATH"
    if [[ ! -f "$AI_PROMPT_PATH" ]]; then
        print_color "$RED" "Warning: ai-prompt not found at $AI_PROMPT_PATH"
    fi

    echo
fi

# API Key Configuration
print_color "$BLUE" "========================================="
print_color "$BLUE" "   API Key Configuration"
print_color "$BLUE" "========================================="
echo

print_color "$YELLOW" "Which AI provider(s) would you like to use?"
prompt_choice "" "  1) OpenAI only
  2) Anthropic/Claude only
  3) Both (recommended for flexibility)" "PROVIDER_CHOICE" "3"

echo

# Initialize variables
OPENAI_KEY=""
ANTHROPIC_KEY=""
AVAILABLE_MODELS=""
AI_CLI_ENV_PATH="$ROOT_DIR/tools/ai-cli/.env"

# If manual installation, ask for ai-cli path
if [[ "$INSTALL_METHOD" == "2" ]]; then
    AI_CLI_DIR="$(dirname "$AI_PROMPT_PATH")"
    AI_CLI_ENV_PATH="$AI_CLI_DIR/.env"
fi

# Collect API keys
if [[ "$PROVIDER_CHOICE" == "1" ]] || [[ "$PROVIDER_CHOICE" == "3" ]]; then
    print_color "$YELLOW" "OpenAI API Key"
    print_color "$BLUE" "Get yours at: https://platform.openai.com/api-keys"
    prompt_secure "Enter OpenAI API key (will be hidden)" "OPENAI_KEY"
    AVAILABLE_MODELS="${AVAILABLE_MODELS}OpenAI Models:
  - gpt-5 (most advanced)
  - gpt-5-mini (lighter, faster, default)
  - gpt-5-nano (ultra-light)
  - gpt-4o (GPT-4 optimized)
  - gpt-4o-mini (GPT-4 optimized mini)
"
fi

if [[ "$PROVIDER_CHOICE" == "2" ]] || [[ "$PROVIDER_CHOICE" == "3" ]]; then
    print_color "$YELLOW" "Anthropic/Claude API Key"
    print_color "$BLUE" "Get yours at: https://console.anthropic.com/settings/keys"
    prompt_secure "Enter Anthropic API key (will be hidden)" "ANTHROPIC_KEY"
    if [[ -n "$AVAILABLE_MODELS" ]]; then
        AVAILABLE_MODELS="${AVAILABLE_MODELS}
"
    fi
    AVAILABLE_MODELS="${AVAILABLE_MODELS}Anthropic Claude Models:
  - claude-opus-4-1 (most powerful)
  - claude-sonnet-4 (balanced)
  - claude-haiku-3-5 (fast & efficient)
  - claude-sonnet-3-5 (previous Sonnet)"
fi

# Create .env file for ai-cli
echo
print_color "$YELLOW" "Creating .env file for ai-cli..."

cat > "$AI_CLI_ENV_PATH" <<EOF
# AI Prompt Executor Environment Variables
# Generated by Podcast Insights Setup

EOF

if [[ -n "$OPENAI_KEY" ]]; then
    echo "OPENAI_API_KEY=$OPENAI_KEY" >> "$AI_CLI_ENV_PATH"
fi

if [[ -n "$ANTHROPIC_KEY" ]]; then
    echo "ANTHROPIC_API_KEY=$ANTHROPIC_KEY" >> "$AI_CLI_ENV_PATH"
fi

print_color "$GREEN" "✓ API keys saved to $AI_CLI_ENV_PATH"
echo

# Model Selection
print_color "$BLUE" "========================================="
print_color "$BLUE" "   Model Selection"
print_color "$BLUE" "========================================="
echo

print_color "$YELLOW" "Available models based on your API keys:"
echo "$AVAILABLE_MODELS"
echo

DEFAULT_MODEL="gpt-5-mini"
if [[ "$PROVIDER_CHOICE" == "2" ]]; then
    DEFAULT_MODEL="claude-haiku-3-5"
fi

prompt_user "Which model would you like to use for insights generation?" "SELECTED_MODEL" "$DEFAULT_MODEL"
echo

# RSS Feeds Configuration
print_color "$BLUE" "========================================="
print_color "$BLUE" "   RSS Feed Configuration (Optional)"
print_color "$BLUE" "========================================="
echo

print_color "$YELLOW" "Would you like to add RSS feeds now? (you can add more later)"
prompt_choice "" "  1) Yes, add feeds
  2) No, skip for now" "ADD_FEEDS" "2"

FEEDS_CONFIG=""
if [[ "$ADD_FEEDS" == "1" ]]; then
    echo
    print_color "$YELLOW" "Enter RSS feeds (press Enter with empty line to finish):"
    while true; do
        prompt_user "Feed URL (or press Enter to finish)" "FEED_URL" ""
        if [[ -z "$FEED_URL" ]]; then
            break
        fi
        prompt_user "Feed name" "FEED_NAME" ""
        FEEDS_CONFIG="${FEEDS_CONFIG}  - url: $FEED_URL
    name: $FEED_NAME
"
    done
else
    # Use default example feeds
    FEEDS_CONFIG="  # Example feeds - replace with your own
  - url: https://feeds.megaphone.fm/hubermanlab
    name: Huberman Lab
  - url: https://api.substack.com/feed/podcast/10845.rss
    name: How I Write"
fi

# Create config.yaml
print_color "$YELLOW" "Creating config.yaml..."

cat > "$ROOT_DIR/config.yaml" <<EOF
# Podcast Insights Configuration
# Generated by setup.sh on $(date)

storage:
  data_dir: ./data
  temp_dir: ./data/_tmp

runtime:
  max_retries: 3
  retry_backoff_seconds: 5

tools:
  # Tool paths (automatically configured by setup)
  transcribe_cmd: "$TRANSCRIBE_PATH \"{audio}\" -o \"{transcript}\""
  insights_cmd: "$AI_PROMPT_PATH --prompt \"{transcript}\" --system-prompt podcast-insights --output-path \"{episode_dir}\" --output-name \"{insights_file}\" --model $SELECTED_MODEL"

  # To change the AI model later, edit the --model parameter above
  # Available models: $SELECTED_MODEL
  # You can also update API keys in: $AI_CLI_ENV_PATH

feeds:
$FEEDS_CONFIG
EOF

print_color "$GREEN" "✓ Configuration saved to config.yaml"
echo

# Set up main virtualenv
print_color "$YELLOW" "Setting up main application virtualenv..."
if [[ ! -d "$ROOT_DIR/.venv" ]]; then
    "$PY_BIN" -m venv "$ROOT_DIR/.venv"
fi
"$ROOT_DIR/.venv/bin/pip" install --upgrade pip >/dev/null 2>&1
"$ROOT_DIR/.venv/bin/pip" install -r "$ROOT_DIR/requirements.txt" >/dev/null 2>&1
print_color "$GREEN" "✓ Main virtualenv ready"
echo

# Final success message
print_color "$GREEN" "========================================="
print_color "$GREEN" "   Setup Complete!"
print_color "$GREEN" "========================================="
echo
print_color "$BLUE" "You can now run the application with:"
print_color "$YELLOW" "  ./podcast-insights"
echo
print_color "$BLUE" "To update the bundled tools later:"
print_color "$YELLOW" "  ./update.sh"
echo
print_color "$BLUE" "Configuration locations:"
print_color "$YELLOW" "  - Main config: $ROOT_DIR/config.yaml"
print_color "$YELLOW" "  - AI API keys: $AI_CLI_ENV_PATH"
print_color "$YELLOW" "  - Selected model: $SELECTED_MODEL (edit in config.yaml to change)"
echo
print_color "$GREEN" "Happy podcasting!"
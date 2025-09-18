#!/usr/bin/env bash
set -euo pipefail

# Podcast Insights Update Script
# Updates the bundled tools (git submodules) to their latest versions

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

print_color "$BLUE" "========================================="
print_color "$BLUE" "   Podcast Insights Tool Updater"
print_color "$BLUE" "========================================="
echo

# Check if submodules exist
if [[ ! -d "$ROOT_DIR/tools/ai-cli" ]] || [[ ! -d "$ROOT_DIR/tools/podcast-transcription" ]]; then
    print_color "$RED" "Error: Submodules not found. Please run ./setup.sh first."
    exit 1
fi

# Store current directory
ORIGINAL_DIR="$(pwd)"

# Function to update a submodule
update_submodule() {
    local name="$1"
    local path="$2"

    print_color "$YELLOW" "Updating $name..."

    cd "$path"

    # Get current commit
    CURRENT_COMMIT="$(git rev-parse --short HEAD)"

    # Fetch updates
    git fetch origin >/dev/null 2>&1

    # Get latest commit
    LATEST_COMMIT="$(git rev-parse --short origin/main)"

    if [[ "$CURRENT_COMMIT" == "$LATEST_COMMIT" ]]; then
        print_color "$GREEN" "✓ $name is already up to date (commit: $CURRENT_COMMIT)"
    else
        # Pull latest changes
        git pull origin main >/dev/null 2>&1
        NEW_COMMIT="$(git rev-parse --short HEAD)"
        print_color "$GREEN" "✓ $name updated from $CURRENT_COMMIT to $NEW_COMMIT"

        # Update dependencies if requirements.txt changed
        if git diff "$CURRENT_COMMIT" "$NEW_COMMIT" --name-only | grep -q "requirements.txt"; then
            print_color "$YELLOW" "  Updating dependencies for $name..."

            # Determine virtualenv path
            if [[ -d ".venv" ]]; then
                VENV_PATH=".venv"
            elif [[ -d "venv" ]]; then
                VENV_PATH="venv"
            else
                print_color "$YELLOW" "  Warning: No virtualenv found for $name"
                return
            fi

            # Update dependencies
            "$VENV_PATH/bin/pip" install --upgrade -r requirements.txt >/dev/null 2>&1
            print_color "$GREEN" "  ✓ Dependencies updated"
        fi
    fi

    cd "$ROOT_DIR"
}

# Update ai-cli
update_submodule "ai-cli" "$ROOT_DIR/tools/ai-cli"
echo

# Update podcast-transcription
update_submodule "podcast-transcription" "$ROOT_DIR/tools/podcast-transcription"
echo

# Alternative method using git submodule command
print_color "$YELLOW" "Syncing submodule configuration..."
cd "$ROOT_DIR"
git submodule update --remote --merge >/dev/null 2>&1
print_color "$GREEN" "✓ Submodule configuration synced"
echo

# Return to original directory
cd "$ORIGINAL_DIR"

print_color "$GREEN" "========================================="
print_color "$GREEN" "   Update Complete!"
print_color "$GREEN" "========================================="
echo
print_color "$BLUE" "All tools have been checked and updated to their latest versions."
print_color "$BLUE" "You can now run: ./podcast-insights"
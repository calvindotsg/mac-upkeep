#!/usr/bin/env bash
# Record a mac-upkeep demo GIF using asciinema + agg.
#
# Prerequisites:
#   brew install asciinema agg
#
# Usage:
#   ./demo/record.sh          # record + convert
#   ./demo/record.sh convert  # re-convert existing demo.cast
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
CAST="$REPO_ROOT/demo.cast"
GIF="$SCRIPT_DIR/demo.gif"

# Ayu Dark theme: bg, fg, ANSI 0-7, bright 8-15
AYU_DARK="0A0E14,B3B1AD,01060E,EA6C73,91B362,F9AF4F,53BDFA,FAE994,90E1C6,C7C7C7,686868,F07178,C2D94C,FFB454,59C2FF,FFEE99,95E6CB,FFFFFF"

convert() {
    echo "Converting ${CAST} → ${GIF}..."
    agg --speed 3 \
        --theme "$AYU_DARK" \
        --idle-time-limit 2 \
        --font-size 16 \
        --font-family "FiraCode Nerd Font" \
        "$CAST" "$GIF"
    echo "Done: $(du -h "$GIF" | cut -f1) ${GIF}"
}

if [[ "${1:-}" == "convert" ]]; then
    convert
    exit 0
fi

echo "Recording mac-upkeep demo..."
echo "Run 'mac-upkeep run' in the session, then press ctrl+d to stop."
asciinema rec --idle-time-limit 3 "$CAST"
convert

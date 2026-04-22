#!/usr/bin/env bash
# Daily cron helper: activates venv and runs scraper.py once.
# Example crontab (every day at 08:00):
#   0 8 * * * /Users/YOU/path/to/foreign\ magazine\ grab\ daily/run_daily.sh

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ -d .venv ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

python3 scraper.py "$@" >> logs/cron.log 2>&1

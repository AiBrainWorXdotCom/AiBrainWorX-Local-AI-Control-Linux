#!/usr/bin/env bash
set -euo pipefail
APP_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python3 -c 'import sys,sqlite3,ssl; assert sys.version_info >= (3,11), "Python 3.11 or newer is required"; print("Python, SQLite and SSL are ready.")'
printf '%s\n' 'No packages, engines or models were downloaded.' 'Run: bash Launch.sh'
exec python3 -B -m unittest discover -s "$APP_ROOT/tests" -q

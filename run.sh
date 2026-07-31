#!/usr/bin/env bash
# Start the dev server. Always via `python3 -m` so the venv's Python is used.
set -e
cd "$(dirname "$0")"
[ -d venv ] || python3 -m venv venv
source venv/bin/activate
pip install -q -r requirements.txt
python3 -m uvicorn app.main:app --reload --port 8000

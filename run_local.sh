#!/usr/bin/env bash
set -euo pipefail

if [ ! -d ".venv" ]; then
  python -m venv .venv
fi

source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install firefox
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

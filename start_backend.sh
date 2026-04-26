#!/bin/bash
cd "$(dirname "$0")/backend"

if [ ! -d ".venv" ]; then
  python3.12 -m venv .venv
  .venv/bin/pip install -r ../requirements.txt
fi

.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 --reload

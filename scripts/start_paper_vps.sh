#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if git remote get-url origin >/dev/null 2>&1; then
  git fetch origin
  git pull origin main
fi

cp profiles/aggressive_base_paper.env .env
python src/startup_validation.py
python src/main.py

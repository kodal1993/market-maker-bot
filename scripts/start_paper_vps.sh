#!/usr/bin/env bash
set -euo pipefail

cd /root/market-maker-bot

git fetch origin
git pull origin main
cp profiles/aggressive_base_paper.env .env
python src/startup_validation.py
python src/main.py

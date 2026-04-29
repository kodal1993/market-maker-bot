#!/usr/bin/env bash
set -euo pipefail

cd /root/market-maker-bot
python scripts/monitor_aggressive_paper.py --once

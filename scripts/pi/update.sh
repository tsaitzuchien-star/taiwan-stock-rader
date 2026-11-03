#!/usr/bin/env bash
# 更新程式到 GitHub 最新版並重新啟動戰情室：bash scripts/pi/update.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
git pull --ff-only
.venv/bin/pip install -q -r requirements.txt
sudo systemctl restart taiwan-radar
echo "已更新並重新啟動。"

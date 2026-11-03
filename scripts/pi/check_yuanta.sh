#!/usr/bin/env bash
# 元大連線測試（只查詢庫存與報價，不會下單）：bash scripts/pi/check_yuanta.sh
# 注意：測試前先停掉戰情室（sudo systemctl stop taiwan-radar），避免同一帳號重複登入。
set -euo pipefail
cd "$(dirname "$0")/../.."
SDK_DIR="$(sed -n "s/^sdk_dir *= *['\"]\([^'\"]*\)['\"].*/\1/p" config.local.toml | head -1)"
export DOTNET_ROOT="$HOME/.dotnet" LD_LIBRARY_PATH="${SDK_DIR:-$HOME/yuanta-sdk}"
.venv/bin/python -m radar.yuanta.check

#!/usr/bin/env bash
# 台股雷達：樹莓派 5（Raspberry Pi OS 64 位元）一鍵安裝
# 用法：在專案資料夾執行  bash scripts/pi/install.sh
# 會做：安裝系統套件、設定台北時區、安裝 .NET 8、建立 Python 環境、安裝開機自動啟動的服務。
# 不會碰你的帳號密碼與憑證；那些只放在你自己的 config.local.toml 和 .pfx 檔。
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
RUN_USER="$(id -un)"
SDK_DIR="${YUANTA_SDK_DIR:-$HOME/yuanta-sdk}"
DOTNET_DIR="$HOME/.dotnet"
SERVICE=/etc/systemd/system/taiwan-radar.service

if [ "$(id -u)" = "0" ]; then
  echo "請用一般使用者執行（不要加 sudo），需要權限時腳本會自己問密碼。"; exit 1
fi
if [ "$(uname -m)" != "aarch64" ]; then
  echo "提醒：這台不是 64 位元 ARM（目前是 $(uname -m)）。樹莓派請安裝 Raspberry Pi OS (64-bit)。"
fi

echo "== 1/5 安裝系統套件、設定台北時區 =="
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-dev git curl sqlite3 libffi-dev libicu-dev
sudo timedatectl set-timezone Asia/Taipei

echo "== 2/5 安裝 .NET 8（元大元件需要）到 $DOTNET_DIR =="
if [ ! -x "$DOTNET_DIR/dotnet" ] || ! "$DOTNET_DIR/dotnet" --list-runtimes | grep -q "Microsoft.AspNetCore.App 8\."; then
  curl -fsSL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh
  bash /tmp/dotnet-install.sh --channel 8.0 --runtime aspnetcore --install-dir "$DOTNET_DIR"
fi
"$DOTNET_DIR/dotnet" --list-runtimes

echo "== 3/5 建立 Python 環境並安裝套件（第一次約 5–10 分鐘）=="
cd "$APP_DIR"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "== 4/5 準備設定檔 =="
mkdir -p data logs "$SDK_DIR"
if [ ! -f config.local.toml ]; then
  cp config.example.toml config.local.toml
  echo "已建立 config.local.toml，請之後填入元大帳號、密碼與 cert_path。"
fi
# 範例檔裡是 Windows 路徑：換成樹莓派上的元大元件資料夾（已經改過就不動）
if grep -qE "^sdk_dir *= *['\"]?[A-Za-z]:" config.local.toml; then
  sed -i "s|^sdk_dir *=.*|sdk_dir = '$SDK_DIR'   # 元大 linux-arm64 元件資料夾|" config.local.toml
fi
chmod 600 config.local.toml
if [ ! -f "$SDK_DIR/YuantaSparkAPI.dll" ]; then
  echo "提醒：$SDK_DIR 裡還沒有元大元件。請把 YuantaSparkAPI_linux-arm64_Python 裡的所有檔案放進去。"
fi

echo "== 5/5 安裝開機自動啟動服務 =="
sed -e "s#@USER@#$RUN_USER#g" -e "s#@APP_DIR@#$APP_DIR#g" -e "s#@SDK_DIR@#$SDK_DIR#g" \
    -e "s#@DOTNET_DIR@#$DOTNET_DIR#g" "$APP_DIR/scripts/pi/taiwan-radar.service" | sudo tee "$SERVICE" >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now taiwan-radar
sleep 5
systemctl --no-pager --lines=5 status taiwan-radar || true

echo
echo "完成！戰情室在這台樹莓派的 http://127.0.0.1:8600 執行，開機會自動啟動，平日 18:30 自動更新資料庫。"
echo "看即時紀錄：journalctl -u taiwan-radar -f      重新啟動：sudo systemctl restart taiwan-radar"

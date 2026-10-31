"""python -m radar.api：啟動戰情室並自動打開瀏覽器。"""
from __future__ import annotations

import argparse
import threading
import webbrowser


def main() -> None:
    p = argparse.ArgumentParser(description="台股雷達戰情室")
    p.add_argument("--port", type=int, default=8600)
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args()
    import uvicorn
    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"戰情室已啟動：{url}（關閉這個視窗即停止）")
    # 只綁 127.0.0.1：只有這台電腦能連，帳務資料不會對外
    uvicorn.run("radar.api.server:create_app", factory=True, host="127.0.0.1", port=args.port,
                log_level="warning")


if __name__ == "__main__":
    main()

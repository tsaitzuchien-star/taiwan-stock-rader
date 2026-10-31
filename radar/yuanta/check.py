"""元大連線測試：python -m radar.yuanta.check

依 config.local.toml 的 [yuanta] 設定連線、登入，並查詢庫存與 2330 台積電報價。
只做查詢，不會下單。加上 --uat 會改用元大公開測試帳號（不需要你的帳號密碼）。
"""
from __future__ import annotations

import argparse
import logging
import sys

from ..config import load_settings
from .client import YuantaClient, YuantaConfig, YuantaError

UAT_ACCOUNT = ("S98875005091", "1234")  # 元大範例程式公開的測試帳號


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="元大 Spark API 連線測試（唯讀）")
    p.add_argument("--uat", action="store_true", help="用元大公開測試帳號連測試環境")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

    cfg = YuantaConfig.from_dict(load_settings().yuanta)
    if args.uat:
        cfg.account, cfg.password = UAT_ACCOUNT
        cfg.environment = "UAT"
        cfg.cert_path = cfg.cert_password = ""
    print(f"環境：{cfg.environment}　帳號：{cfg.account[:5] + '*' * 7 if cfg.account else '(未設定)'}")
    print(f"元大元件資料夾：{cfg.sdk_dir or '(未設定)'}")
    problems = cfg.problems()
    if problems:
        print("設定有問題：\n  - " + "\n  - ".join(problems))
        return 2

    client = YuantaClient(cfg)
    try:
        print("1/3 連線並登入…")
        info = client.connect()
        print(f"    ✅ 登入成功 {info['code']} {info['message']}")
        print("2/3 查詢庫存…")
        h = client.holdings()
        print(f"    ✅ 庫存 {len(h)} 檔")
        if not h.empty:
            print(h[["股票代號", "股票名稱", "股數", "未實現損益"]].to_string(index=False))
        print("3/3 查詢 2330 台積電報價…")
        q = client.snapshot([("2330", "TWSE")]).get("2330")
        if q:
            print(f"    ✅ 成交價 {q['price']}　昨收 {q['prev_close']}　總量 {q['total_vol']} 張　時間 {q['time']}")
        else:
            print("    ⚠️ 沒有收到 2330 報價")
        print("\n全部完成，元大連線正常。")
        return 0
    except YuantaError as e:
        print(f"\n❌ {e}")
        return 1
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"\n❌ 未預期的錯誤：{type(e).__name__}: {e}")
        return 1
    finally:
        print("\n最近的元大訊息：")
        for m in list(client.messages)[-15:]:
            print("   ", m)
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

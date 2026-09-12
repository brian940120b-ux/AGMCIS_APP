"""
上線前檢查。

    python scripts/preflight.py

一個問題:**這套系統現在可以跑在生產環境嗎?**

檢查項目分三級:

    BLOCKER   會導致資金損失或系統失控。沒修好之前不要跑。
    WARNING   會降低安全性或讓結果失真,但不會立刻出事。
    INFO      狀態說明。

離開碼:0 = 沒有 BLOCKER,1 = 有 BLOCKER。

⚠️ 這支腳本**唯讀**。它不改設定、不下單、不平倉。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BLOCKER, WARNING, INFO, OK = "BLOCKER", "WARNING", "INFO", "OK"

ICONS = {BLOCKER: "⛔", WARNING: "⚠️ ", INFO: "ℹ️ ", OK: "✓"}

results = []


def check(name, level, detail=""):
    results.append((name, level, detail))
    print(f"  {ICONS[level]} [{level}] {name}")
    for line in str(detail).split("\n"):
        if line:
            print(f"        {line}")


def section(title):
    print(f"\n{title}")
    print("-" * len(title))


# ---------------- 1. 秘密 ----------------

def check_secrets():
    section("1. 金鑰與秘密")

    from agmcis.config import settings

    if not os.getenv("DB_PASSWORD"):
        check("資料庫密碼", BLOCKER,
              "DB_PASSWORD 未設定。舊版曾把密碼寫在 db.py 裡並提交進 git,\n"
              "請改用環境變數,並且確認那把舊密碼已經輪替過。")
    else:
        check("資料庫密碼", OK, "由環境變數提供")

    if settings.has_exchange_credentials():
        check("交易所金鑰", OK, "已設定(本腳本不會讀出它的值)")
    else:
        check("交易所金鑰", INFO,
              "未設定。公開行情可以運作,私有端點不行。")

    # 金鑰絕不可以出現在版控裡
    repo = Path(__file__).resolve().parents[1]
    tracked_env = (repo / ".env")
    gitignore = (repo / ".gitignore")

    if gitignore.exists() and ".env" in gitignore.read_text(encoding="utf-8"):
        check(".env 已被 gitignore", OK)
    else:
        check(".env 已被 gitignore", BLOCKER,
              ".env 沒有被 gitignore,金鑰有可能被提交進版控。")

    if tracked_env.exists():
        check(".env 存在於機器上", INFO, str(tracked_env))


# ---------------- 2. 交易模式 ----------------

def check_trading_mode():
    section("2. 交易模式")

    from agmcis.config import settings

    mode = str(getattr(settings, "TRADING_MODE", "paper")).lower()

    if mode == "live":
        check("交易模式", WARNING,
              "TRADING_MODE=live。實單能不能真的送出去由 LIVE SAFETY GATE\n"
              "決定,不是由這個設定決定 —— 跑 python scripts/live_gate.py。")
    else:
        check("交易模式", OK, f"{mode}(模擬盤)")

    # 實單路徑存不存在,以及它有沒有被人逐檔讀過並簽章(第七十八節)。
    #
    # 這裡**不判定它該不該存在** —— 那是閘門的事,而且閘門掃的範圍
    # 是整個 execution 套件,不是單一模組。用兩套不同的判斷去回答
    # 同一個問題,遲早會有一邊說有一邊說沒有。
    from agmcis.safety.live_gate import LiveGate

    try:
        found = LiveGate()._scan_live_broker_sources()
    except Exception as exc:
        check("實單路徑", BLOCKER,
              f"掃不到實單路徑({type(exc).__name__}: {exc})——"
              f"掃不動不等於沒有。")
        return

    if not found:
        check("實單路徑", OK, "沒有 LiveBroker,系統無法送出真實訂單")
        return

    files = sorted({where for where, _n, _w in found})
    check("實單路徑", WARNING,
          f"系統裡有會送出真實訂單的程式碼:{'、'.join(files)}。\n"
          f"它要由人逐檔讀過並簽下原始碼雜湊,閘門才會放行 ——\n"
          f"先跑 python scripts/verify_live_broker.py,再跑 "
          f"python scripts/live_confirm.py。")


# ---------------- 3. 風控 ----------------

def check_risk():
    section("3. 風控")

    import risk_limits
    from agmcis.config import settings

    max_leverage = risk_limits.MAX_LEVERAGE
    if max_leverage > 20:
        check("槓桿上限", BLOCKER, f"{max_leverage}x 過高")
    elif max_leverage > 10:
        check("槓桿上限", WARNING, f"{max_leverage}x 偏高")
    else:
        check("槓桿上限", OK, f"{max_leverage}x")

    risk_pct = getattr(settings, "MAX_RISK_PER_TRADE_PCT", None)
    if risk_pct is None:
        check("單筆風險上限", WARNING, "未設定")
    elif risk_pct > 5:
        check("單筆風險上限", BLOCKER, f"{risk_pct}% 過高(一般不超過 2%)")
    elif risk_pct > 2:
        check("單筆風險上限", WARNING, f"{risk_pct}% 偏高")
    else:
        check("單筆風險上限", OK, f"{risk_pct}%")

    from agmcis.risk.kill_switch import get_kill_switch

    status = get_kill_switch().status()

    if status["armed"]:
        check("Kill Switch", WARNING,
              f"目前處於啟動狀態({status['stop_file']} 存在),不會開新倉。")
    else:
        check("Kill Switch", OK, "未啟動")

    if status["can_close_positions"]:
        check("Kill Switch 平倉能力", OK, "已接上 Execution Engine")
    else:
        check("Kill Switch 平倉能力", BLOCKER,
              "panic() 無法平倉。一個按下去不會平倉的緊急按鈕比沒有更危險 ——\n"
              "你會以為自己按過了。")


# ---------------- 4. 資料與規格 ----------------

def check_calibration():
    section("4. 合約規格校準")

    from agmcis.config import settings
    from agmcis.exchange import specs

    report = specs.get_store().calibration_report(settings.WATCHLIST_SYMBOLS)

    if not report["calibrated"]:
        check("規格快照", WARNING,
              "沒有合約規格快照。維持保證金率、費率、資金費率全部是保守猜測值 ——\n"
              "強平價與成本都只是估計。在 VPS 上執行:\n"
              "  .venv/bin/python scripts/verify_bingx.py --write-specs")
    elif report["stale"]:
        check("規格快照", WARNING,
              f"快照已過期({report['age_days']} 天)。費率與分層會變。")
    else:
        check("規格快照", OK, f"{report['captured_at']}")


# ---------------- 5. 資料庫 ----------------

REQUIRED_TABLES = [
    ("trades", "交易"),
    ("accounts", "帳戶"),
    ("orders", "訂單(Phase 13)"),
    ("order_events", "訂單事件(Phase 13)"),
]

OPTIONAL_TABLES = [
    ("rate_limit_calls", "跨行程限流(Phase 16)"),
    ("rate_limit_cooldown", "跨行程限流冷卻(Phase 16)"),
]


def check_database():
    section("5. 資料庫")

    try:
        from db import transaction
    except Exception as exc:
        check("資料庫連線", BLOCKER, f"載入失敗:{exc}")
        return

    def table_exists(name):
        with transaction() as cur:
            cur.execute("SELECT to_regclass(%s);", (f"public.{name}",))
            return cur.fetchone()[0] is not None

    try:
        for table, label in REQUIRED_TABLES:
            if table_exists(table):
                check(f"資料表 {table}", OK, label)
            else:
                check(f"資料表 {table}", BLOCKER,
                      f"{label} 不存在。請執行 python scripts/migrate.py")
    except Exception as exc:
        check("資料庫連線", BLOCKER, f"{type(exc).__name__}: {exc}")
        return

    from agmcis.config import settings

    for table, label in OPTIONAL_TABLES:
        exists = table_exists(table)
        if exists:
            check(f"資料表 {table}", OK, label)
        elif settings.EXCHANGE_SHARED_RATE_LIMIT:
            check(f"資料表 {table}", BLOCKER,
                  f"EXCHANGE_SHARED_RATE_LIMIT 已開啟但 {label} 不存在。\n"
                  "每次呼叫都會先失敗再降級,那比不開更慢。")
        else:
            check(f"資料表 {table}", INFO, f"{label} 未建立(該功能未啟用)")


# ---------------- 6. 自我檢討 ----------------

def check_self_review():
    section("6. 系統自我檢討")

    try:
        from agmcis.review.self_review import build
        review = build()
    except Exception as exc:
        check("自我檢討", WARNING, f"無法產生:{type(exc).__name__}: {exc}")
        return

    if review.verdict == "LOSING":
        check("績效判定", BLOCKER, review.headline)
    elif review.verdict == "FRAGILE":
        check("績效判定", WARNING, review.headline)
    elif review.verdict == "NOT_ENOUGH_DATA":
        check("績效判定", INFO, review.headline)
    else:
        check("績效判定", OK, review.headline)


CHECKS = [
    check_secrets,
    check_trading_mode,
    check_risk,
    check_calibration,
    check_database,
    check_self_review,
]


def main():
    print("=" * 64)
    print("AGMCIS — 上線前檢查")
    print("=" * 64)

    for fn in CHECKS:
        try:
            fn()
        except Exception as exc:
            check(fn.__name__, BLOCKER, f"檢查本身失敗:{type(exc).__name__}: {exc}")

    print("\n" + "=" * 64)

    counts = {}
    for _, level, _ in results:
        counts[level] = counts.get(level, 0) + 1
    print("結果: " + "  ".join(f"{k} {v}" for k, v in sorted(counts.items())))

    blockers = [name for name, level, _ in results if level == BLOCKER]
    if blockers:
        print("\n必須修好才能上線:")
        for name in blockers:
            print(f"  ⛔ {name}")
        return 1

    print("\n沒有 BLOCKER。")
    print("注意:這只檢查了設定與狀態,不代表策略有優勢 —— 那是 Phase 8 與 15 的問題。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

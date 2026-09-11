"""
BingX 連線唯讀驗證。

在**金鑰所在的機器**(VPS)上執行。金鑰只從 .env 讀,不需要也不應該
貼到任何對話或 log 裡。

    /root/AGMCIS_APP/.venv/bin/python scripts/verify_bingx.py

⚠️ 這支腳本**全程唯讀**:
    不下單、不撤單、不改槓桿、不改保證金模式、不轉帳。
    它只呼叫 fetch_* 系列。

檢查項目:
  1. 公開行情(不需要金鑰)
  2. 伺服器時間偏移 —— 簽章失敗最常見的根因
  3. 合約規則(tick / step / 最小量 / 最小名目 / 槓桿上限)
  4. 私有端點:餘額、持倉、槓桿、持倉模式(需要金鑰)
  5. 提款權限檢查 —— API Key 不該開提款
  6. Standard Futures 支援狀況

離開碼 0 代表全部通過,1 代表有項目失敗。
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agmcis.config import settings  # noqa: E402
from agmcis.core.enums import MarketType  # noqa: E402
from agmcis.core.errors import ExchangeUnavailableError  # noqa: E402
from agmcis.exchange import trading_rules as tr  # noqa: E402
from agmcis.exchange.bingx.adapter import BingXAdapter, STANDARD_UNSUPPORTED_REASON  # noqa: E402

SYMBOL = os.getenv("VERIFY_SYMBOL", "BTC/USDT")

PASS, FAIL, SKIP, WARN = "PASS", "FAIL", "SKIP", "WARN"
results = []


def record(name, status, detail=""):
    results.append((name, status, detail))
    icon = {PASS: "✓", FAIL: "✗", SKIP: "-", WARN: "!"}[status]
    print(f"  {icon} [{status}] {name}")
    if detail:
        for line in str(detail).split("\n"):
            print(f"        {line}")


def section(title):
    print(f"\n{title}")
    print("-" * len(title))


def main():
    print("=" * 64)
    print("AGMCIS — BingX 唯讀連線驗證")
    print("=" * 64)
    print(f"標的: {SYMBOL}")
    print(f"測試環境 (VST): {settings.EXCHANGE_USE_TESTNET}")
    print(f"金鑰已設定: {settings.has_exchange_credentials()}")

    adapter = BingXAdapter()

    # ---------------- 1. 公開行情 ----------------
    section("1. 公開行情(不需要金鑰)")

    try:
        ticker = adapter.get_ticker(SYMBOL)
        record("ticker", PASS, f"最新價 {ticker['price']}  買 {ticker['bid']}  賣 {ticker['ask']}")
    except Exception as exc:
        record("ticker", FAIL, exc)

    try:
        rows = adapter.get_ohlcv(SYMBOL, "1h", 5)
        record("K 線", PASS, f"取得 {len(rows)} 根")
    except Exception as exc:
        record("K 線", FAIL, exc)

    book = adapter.get_order_book(SYMBOL, 20)
    if book:
        record("深度", PASS,
               f"價差 {book['spread']} ({book['spread_pct']:.4f}%)  "
               f"買賣失衡 {book['imbalance']:+.3f}")
    else:
        record("深度", WARN, "取不到,不影響核心功能")

    funding = adapter.get_funding_rate(SYMBOL)
    if funding:
        record("資金費率", PASS, f"{funding['funding_rate']}")
    else:
        record("資金費率", WARN, "取不到")

    oi = adapter.get_open_interest(SYMBOL)
    record("持倉量", PASS if oi else WARN, oi.get("open_interest") if oi else "取不到")

    # ---------------- 2. 伺服器時間 ----------------
    section("2. 伺服器時間同步")

    offset = adapter.sync_server_time()
    if offset is None:
        record("時鐘偏移", FAIL, "無法取得伺服器時間")
    else:
        status = adapter.clock_status()
        if status["within_tolerance"]:
            record("時鐘偏移", PASS, f"{offset:+.0f} ms(容許 ±{status['tolerance_ms']} ms)")
        else:
            record("時鐘偏移", FAIL,
                   f"{offset:+.0f} ms 超過容許值 ±{status['tolerance_ms']} ms。\n"
                   "這會造成簽章被拒,而錯誤訊息看起來像 API Key 有問題。\n"
                   "請在伺服器上檢查 NTP:timedatectl status")

    # ---------------- 3. 合約規則 ----------------
    section("3. 合約規則(動態取得,不寫死)")

    try:
        rules = adapter.get_trading_rules(SYMBOL, MarketType.PERPETUAL)
        record("合約規則", PASS,
               f"tick={rules.tick_size}  step={rules.step_size}\n"
               f"最小量={rules.min_qty}  最大量={rules.max_qty}\n"
               f"最小名目={rules.min_notional}  合約大小={rules.contract_size}\n"
               f"槓桿上限={rules.max_leverage}x")

        price = adapter.get_ticker(SYMBOL)["price"]
        qty, problems = tr.quantity_for_notional(1000, price, rules)
        if qty:
            record("名目換算", PASS, f"1000 USDT @ {price} -> {qty} 張")
        else:
            record("名目換算", WARN, "; ".join(problems))
    except Exception as exc:
        record("合約規則", FAIL, exc)

    try:
        markets = adapter.list_markets(MarketType.PERPETUAL)
        record("合約清單", PASS, f"{len(markets)} 個可交易的 USDT 永續")
    except Exception as exc:
        record("合約清單", FAIL, exc)

    # ---------------- 4. 私有端點 ----------------
    section("4. 私有端點(需要金鑰,全程唯讀)")

    if not settings.has_exchange_credentials():
        record("餘額", SKIP, "未設定 BINGX_API_KEY / BINGX_API_SECRET")
        record("持倉", SKIP, "同上")
    else:
        try:
            balance = adapter.get_balance()
            usdt = (balance.get("USDT") or {}) if isinstance(balance, dict) else {}
            record("餘額", PASS,
                   f"USDT 可用 {usdt.get('free')}  已用 {usdt.get('used')}  "
                   f"總計 {usdt.get('total')}")
        except Exception as exc:
            record("餘額", FAIL, f"{type(exc).__name__}: {exc}")

        try:
            positions = adapter.get_positions()
            live = [p for p in positions if (p.get("contracts") or 0)]
            record("持倉", PASS, f"共 {len(positions)} 筆,其中 {len(live)} 筆有部位")
            for p in live[:5]:
                print(f"        {p.get('symbol')} {p.get('side')} "
                      f"{p.get('contracts')} @ {p.get('entryPrice')}")
        except Exception as exc:
            record("持倉", FAIL, f"{type(exc).__name__}: {exc}")

        try:
            mode = adapter.get_position_mode(SYMBOL)
            record("持倉模式", PASS, f"{mode}(下單的 positionSide 取決於這個)")
        except Exception as exc:
            record("持倉模式", WARN, f"{type(exc).__name__}: {exc}")

        try:
            lev = adapter.get_leverage(SYMBOL)
            record("目前槓桿", PASS, lev)
        except Exception as exc:
            record("目前槓桿", WARN, f"{type(exc).__name__}: {exc}")

    # ---------------- 5. 金鑰權限 ----------------
    section("5. 金鑰權限(安全檢查)")

    if not settings.has_exchange_credentials():
        record("提款權限", SKIP, "未設定金鑰")
    else:
        # 只讀取設定,不執行任何提款動作
        try:
            exchange = adapter._instance(MarketType.PERPETUAL)
            can_withdraw = bool(exchange.has.get("withdraw"))
            record(
                "提款權限", WARN if can_withdraw else PASS,
                "ccxt 宣告支援提款,請到 BingX 後台確認這把金鑰的提款權限已關閉"
                if can_withdraw else "本函式庫未啟用提款",
            )
        except Exception as exc:
            record("提款權限", WARN, exc)

        print("\n        提醒:這把 API Key 應該只開「讀取」與(之後)「交易」,")
        print("        提款權限必須關閉,並且設定 IP 白名單。")

    # ---------------- 6. Standard Futures ----------------
    section("6. Standard Futures 支援狀況")

    try:
        adapter.get_ticker(SYMBOL, MarketType.STANDARD)
        record("Standard Futures", WARN, "意外地沒有被拒絕,請回報")
    except ExchangeUnavailableError:
        record("Standard Futures", PASS,
               "已明確拒絕(預期行為)。\n" + STANDARD_UNSUPPORTED_REASON)

    # ---------------- 7. 限流狀態 ----------------
    section("7. 限流器")
    status = adapter.rate_limiter.status()
    record("限流器", PASS,
           f"額度 {status['used']}/{status['max_calls']} 每 {status['period_seconds']}s  "
           f"節流次數 {status['throttled_count']}  冷卻次數 {status['cooldown_count']}")

    # ---------------- 總結 ----------------
    print("\n" + "=" * 64)
    counts = {}
    for _, status, _ in results:
        counts[status] = counts.get(status, 0) + 1

    print("結果: " + "  ".join(f"{k} {v}" for k, v in sorted(counts.items())))

    failed = [name for name, status, _ in results if status == FAIL]
    if failed:
        print("\n失敗項目:")
        for name in failed:
            print(f"  ✗ {name}")
        print("\n在這些項目修好之前,不要進入下一個 Phase。")
        return 1

    print("\n全部通過。注意這只驗證了唯讀路徑 ——")
    print("下單路徑需要 Trading Rules Engine(Phase 4)與 Risk Engine(Phase 5)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

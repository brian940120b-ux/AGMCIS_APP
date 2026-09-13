"""
BingX 連線唯讀驗證。

在**金鑰所在的機器**(VPS)上執行。金鑰只從 .env 讀,不需要也不應該
貼到任何對話或 log 裡。

    /root/AGMCIS_APP/.venv/bin/python scripts/verify_bingx.py

    # 順便把合約規格寫成快照,讓系統用真實規格取代保守猜測值
    /root/AGMCIS_APP/.venv/bin/python scripts/verify_bingx.py --write-specs

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
  7. 限流器狀態
  8. (--write-specs)擷取合約規格快照

⚠️ --write-specs 一樣是唯讀操作:它只是把已經抓到的 fetch_* 結果寫成 JSON。

離開碼 0 代表全部通過,1 代表有項目失敗。
"""
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agmcis.config import settings  # noqa: E402
from agmcis.core.enums import MarketType  # noqa: E402
from agmcis.core.errors import ExchangeUnavailableError  # noqa: E402
from agmcis.exchange import specs as specs_module  # noqa: E402
from agmcis.exchange import trading_rules as tr  # noqa: E402
from agmcis.exchange.bingx.adapter import BingXAdapter, STANDARD_UNSUPPORTED_REASON  # noqa: E402

SYMBOL = os.getenv("VERIFY_SYMBOL", "BTC/USDT")

# --write-specs 時要擷取哪些標的。預設抓 watchlist,可用環境變數覆蓋。
SPEC_SYMBOLS = [
    s.strip() for s in os.getenv(
        "VERIFY_SPEC_SYMBOLS", ",".join(settings.WATCHLIST_SYMBOLS),
    ).split(",") if s.strip()
]

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


def _write_specs(adapter):
    """
    把交易所實際回應寫成規格快照。

    **全程唯讀**:只呼叫 fetch_* 系列,不下單、不改槓桿。

    維持保證金率不是每個交易所都從 API 給得出來。拿不到就**留空**,
    讓系統知道那一項仍然是猜的 —— 填一個看起來合理的數字進去,
    會讓「已校準」這件事變成謊話。
    """
    snapshot = specs_module.SpecSnapshot(
        exchange="bingx",
        testnet=bool(settings.EXCHANGE_USE_TESTNET),
        captured_at=datetime.now(timezone.utc).isoformat(),
    )

    for symbol in SPEC_SYMBOLS:
        try:
            rules = adapter.get_trading_rules(symbol, MarketType.PERPETUAL)
        except Exception as exc:
            record(f"規格 {symbol}", FAIL, f"取不到合約規則:{exc}")
            continue

        spec = specs_module.ContractSpec(
            symbol=symbol,
            market_type=MarketType.PERPETUAL.value,
            tick_size=rules.tick_size,
            step_size=rules.step_size,
            min_qty=rules.min_qty,
            max_qty=rules.max_qty,
            min_notional=rules.min_notional,
            contract_size=rules.contract_size,
            max_leverage=rules.max_leverage,
        )

        # 費率:ccxt 的 market 結構通常帶 taker / maker
        market = _market_of(adapter, symbol)
        if market:
            if market.get("taker") is not None:
                spec.taker_fee = float(market["taker"])
            if market.get("maker") is not None:
                spec.maker_fee = float(market["maker"])

        # 資金費率:當下的實際值,不是平均值
        try:
            funding = adapter.get_funding_rate(symbol)
            if funding and funding.get("funding_rate") is not None:
                spec.funding_rate_8h = float(funding["funding_rate"])
        except Exception as exc:
            record(f"資金費率 {symbol}", WARN, f"取不到:{exc}")

        # 維持保證金分層。倉位越大維持保證金率越高、強平價越近 ——
        # 用單一數字會低估大倉位的強平風險。
        tiers = _leverage_tiers(adapter, symbol)
        if tiers:
            spec.maintenance_margin_tiers = tiers
            spec.maintenance_margin_ratio = float(tiers[0]["mmr"])
            record(f"維持保證金分層 {symbol}", PASS, f"{len(tiers)} 層")
        else:
            record(
                f"維持保證金分層 {symbol}", WARN,
                "API 沒有提供分層資料。系統會用保守預設值,\n"
                "大倉位的強平價會被低估。可依官方合約分層文件手動補進\n"
                "contracts[symbol].maintenance_margin_tiers。",
            )

        snapshot.contracts[symbol] = spec

        missing = [
            name for name in ("maintenance_margin_ratio", "taker_fee",
                              "maker_fee", "funding_rate_8h")
            if getattr(spec, name) is None
        ]
        status = WARN if missing else PASS
        record(f"規格 {symbol}", status,
               f"tick={spec.tick_size} step={spec.step_size} "
               f"最小量={spec.min_qty} 槓桿上限={spec.max_leverage}\n"
               f"taker={spec.taker_fee} maker={spec.maker_fee} "
               f"funding={spec.funding_rate_8h}"
               + (f"\n仍缺(系統會繼續用猜測值):{', '.join(missing)}" if missing else ""))

    missing_tiers = [
        symbol for symbol, spec in snapshot.contracts.items()
        if not spec.maintenance_margin_tiers
    ]
    if missing_tiers:
        snapshot.notes.append(
            "以下標的取不到維持保證金分層:" + ", ".join(missing_tiers) + "。"
            "需依官方合約分層文件手動補入 "
            "contracts[symbol].maintenance_margin_tiers "
            "(格式:[{notional_floor, mmr, max_leverage}, ...])。"
            "在補上之前,大倉位的強平價會被低估。"
        )

    path = specs_module.write_snapshot(snapshot)
    record("寫入快照", PASS, f"{path}(共 {len(snapshot.contracts)} 個合約)")
    print("\n        這份快照不進版控 —— 不同帳戶的費率不同,VIP 等級也會變。")
    print("        費率或分層變動後請重新執行。")


def _leverage_tiers(adapter, symbol):
    """
    從 ccxt 取維持保證金分層。取不到就回 None ——
    **不要自己編一組看起來合理的數字**,那會讓「已校準」變成謊話。
    """
    try:
        exchange = adapter._instance(MarketType.PERPETUAL)
        market_symbol = adapter.to_market_symbol(symbol, MarketType.PERPETUAL)

        if not exchange.has.get("fetchMarketLeverageTiers"):
            return None

        raw = exchange.fetch_market_leverage_tiers(market_symbol)
    except Exception:
        return None

    tiers = []
    for item in raw or []:
        mmr = item.get("maintenanceMarginRate")
        if mmr is None:
            continue
        tiers.append({
            "notional_floor": float(item.get("minNotional") or 0),
            "mmr": float(mmr),
            "max_leverage": (
                float(item["maxLeverage"]) if item.get("maxLeverage") else None
            ),
        })

    return sorted(tiers, key=lambda t: t["notional_floor"]) or None


def _market_of(adapter, symbol):
    """從 ccxt 的 markets 取這個合約的原始結構。取不到就回 None。"""
    try:
        exchange = adapter._instance(MarketType.PERPETUAL)
        market_symbol = adapter.to_market_symbol(symbol, MarketType.PERPETUAL)
        return exchange.market(market_symbol)
    except Exception:
        return None


def _live_path_endpoints(adapter):
    """
    跑 LiveBroker 依賴的三個唯讀端點,並把**原始欄位長相**印出來。

    印原始欄位是重點。有一個問題這台機器答不了:單向持倉的時候
    BingX 的 `positionSide` 回什麼?如果回 "BOTH",ccxt 會把
    `side` 設成 "both",而 LiveBroker 的 `_exit_side()` 看到不是
    long / short 就拒絕平倉 —— 那會讓單向持倉的部位平不掉。

    猜錯的代價太大(第五節:不要靠記憶猜),所以這裡不猜,
    直接把你帳戶上的實際值印出來。
    """
    try:
        mode = adapter.get_position_mode()
        hedged = (mode or {}).get("hedged")
        record(
            "持倉模式", PASS if isinstance(hedged, bool) else WARN,
            f"hedged={hedged!r}"
            + ("(雙向持倉:出場單會帶 positionSide)" if hedged is True else "")
            + ("(單向持倉:出場單不帶 positionSide)" if hedged is False else "")
            + ("  ⚠️ 看不懂的回應會讓 LiveBroker 一律不帶 positionSide"
               if not isinstance(hedged, bool) else ""),
        )
    except Exception as exc:
        record("持倉模式", WARN,
               f"{type(exc).__name__}: {exc}\n"
               "LiveBroker 查不到時會一律不帶 positionSide。")

    try:
        orders = adapter.get_open_orders(SYMBOL)
        record("未結掛單查詢", PASS, f"{len(orders)} 張")
    except Exception as exc:
        record("未結掛單查詢", WARN,
               f"{type(exc).__name__}: {exc}\n"
               "LiveBroker 查不到掛單時會把部位視為**沒有停損保護**。")

    try:
        history = adapter.get_order_history(SYMBOL)
        record("訂單歷史查詢", PASS, f"{len(history)} 筆")
        if history:
            sample = history[0]
            record(
                "歷史訂單的 clientOrderId", PASS,
                f"統一欄位 {sample.get('clientOrderId')!r} / "
                f"原始欄位 {(sample.get('info') or {}).get('clientOrderID')!r}",
            )
    except Exception as exc:
        record("訂單歷史查詢", WARN,
               f"{type(exc).__name__}: {exc}\n"
               "對帳查不到歷史時會拋例外,訂單留在「狀態不明」。")

    # ---- 這台機器答不了的那個問題 ----
    try:
        positions = adapter.get_positions()
    except Exception as exc:
        record("部位方向欄位", WARN, f"{type(exc).__name__}: {exc}")
        return

    live = [p for p in positions if p.get("contracts")]

    if not live:
        record("部位方向欄位", SKIP,
               "目前沒有部位,所以看不到 side 實際會是什麼值。\n"
               "**開了第一個部位之後請再跑一次這支腳本。**\n"
               "要確認的是:單向持倉時 side 是 'long'/'short' 還是 'both'。\n"
               "如果是 'both',LiveBroker 會拒絕平倉 —— 那要先修。")
        return

    for position in live:
        side = position.get("side")
        info = position.get("info") or {}
        ok = str(side).lower() in ("long", "short")
        record(
            f"部位方向欄位 {position.get('symbol')}",
            PASS if ok else WARN,
            f"side={side!r}  原始 positionSide={info.get('positionSide')!r}  "
            f"positionAmt={info.get('positionAmt')!r}"
            + ("" if ok else
               "\n⚠️ side 不是 long / short,LiveBroker 會拒絕平倉這個部位。"
               "\n   這是已知的未解問題,見 ROADMAP。"),
        )


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

    # ---------------- 8. 合約規格快照 ----------------
    if "--write-specs" in sys.argv:
        section("8. 合約規格快照(唯讀擷取)")
        _write_specs(adapter)
    else:
        section("8. 合約規格快照")
        record("快照", SKIP,
               "未指定 --write-specs。系統目前用的是保守猜測值 ——\n"
               "維持保證金率、費率、資金費率都不是 BingX 的實際規格。")

    # ---------------- 9. 實單路徑用得到的唯讀端點 ----------------
    #
    # LiveBroker 靠這三個回答三個問題:這個部位有沒有停損、
    # 這張狀態不明的單怎麼了、平倉單要不要帶 positionSide。
    # 三個都是唯讀的,但**三個都沒有在真的 BingX 上跑過**。
    # 在這裡先跑一次,總比第一次實單的時候才發現好。
    section("9. 實單路徑會用到的唯讀端點")

    if not settings.has_exchange_credentials():
        record("實單路徑端點", SKIP, "未設定金鑰")
    else:
        _live_path_endpoints(adapter)

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

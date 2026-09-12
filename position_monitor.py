"""
持倉監控:檢查 TP / SL 並平倉。

Phase 0.5 的修正:
  1. 回傳真實的平倉數。原本 closed_count 與 closed 是寫死的 0 與 [],
     即使實際平了倉,scheduler log 與 /api/scheduler_status 也永遠顯示 0。
  2. 取價或停損為 None 時不再直接比較大小(原本會拋 TypeError,
     被 scheduler 的 except 吞掉,導致該輪所有持倉都不檢查停損)。
  3. 缺停損的倉位改為 WARNING 級別的 unprotected 清單回報,不再靜默跳過。
     沒有停損的倉位等於沒有風險上限,必須讓維運看得到。

position_manager.manage_open_positions() 也改為呼叫這裡的同一份邏輯,
不再有兩套各自實作的平倉判斷。

Phase 10 加入**強制平倉**。判定順序與回測引擎一致:
停損與強平之中,**離進場價較近的那個先觸發**,不是無條件先看強平。
做多停損 99、強平 91 時,價格是先經過 99 的,那筆是正常停損。

原本這裡比對的是輪詢當下的**單一價格**。兩次輪詢之間穿刺停損又彈回來的行情
看不到 —— 但交易所的觸發單會成交。那會讓模擬勝率系統性偏高。

現在改成看輪詢間隔內的 1m K 棒 high / low,規則與回測引擎一致:

  * 做多看 low,做空看 high
  * 停損與強平之中,離進場價較近的那個先觸發
  * K 棒開盤已經穿過觸發價時,成交價是**開盤價**不是觸發價
    (跳空時不可能還在觸發價成交)

取不到 K 棒時退回單一價格判定,並且記錄下來 ——
降級可以接受,安靜地降級不行。

⚠️ 仍然存在的偏誤:1m K 棒還是聚合過的。同一根裡先碰停損還是先碰停利,
   沒有逐筆資料就不知道。這裡沿用回測的假設:**一律當作停損先到**。
   寧可低估績效也不要高估。
"""
from agmcis.config import settings
from database_service import get_open_trades
from direction import is_long, is_short
from logger_service import logger
from market_data import get_price
from notifier import notify_close_trade
from paper_trading import close_paper_trade


LIQUIDATION_REASON = "強制平倉"


def _adverse_level(signal, stoploss, liquidation_price):
    """
    停損與強平之中,離進場價較近的那個會先被觸發。

    回傳 (觸發價, 是否為強平)。兩個都沒有就回 (None, False)。
    """
    if stoploss is None and liquidation_price is None:
        return None, False

    if stoploss is None:
        return liquidation_price, True

    if liquidation_price is None:
        return stoploss, False

    if is_long(signal):
        # 做多時價格往下走,先碰到的是**較高**的那個
        if liquidation_price > stoploss:
            return liquidation_price, True
        return stoploss, False

    # 做空時價格往上走,先碰到的是**較低**的那個
    if liquidation_price < stoploss:
        return liquidation_price, True
    return stoploss, False


def _exit_reason(signal, price, stoploss, takeprofit, liquidation_price=None,
                 high=None, low=None, open_price=None):
    """
    回傳 (平倉原因, 是否為強平, 成交價)。沒有觸發條件則回傳 (None, False, None)。

    high / low 給了就用它們判定觸發(那是輪詢間隔內真的走到過的價格);
    沒給就退回單一價格 —— 行為與 Phase 10 相同。

    stoploss / takeprofit / liquidation_price 都可為 None。
    """
    level, liquidated = _adverse_level(signal, stoploss, liquidation_price)

    # 沒有 high/low 時,單一價格同時扮演兩者
    worst = low if low is not None else price
    best = high if high is not None else price

    if is_short(signal):
        worst, best = best, worst

    if not (is_long(signal) or is_short(signal)):
        return None, False, None

    long_side = is_long(signal)

    # ---- 不利方向:停損 / 強平 ----
    if level is not None:
        hit = worst <= level if long_side else worst >= level
        if hit:
            fill = _fill_price(level, open_price, long_side)
            reason = LIQUIDATION_REASON if liquidated else "自動止損"
            return reason, liquidated, fill

    # ---- 有利方向:停利 ----
    #
    # 同一根 K 棒同時觸及停損與停利時,上面的停損已經先回傳了 ——
    # 那是刻意的。沒有逐筆資料就不知道誰先到,一律假設停損先到,
    # 寧可低估績效也不要高估(與回測引擎同一個假設)。
    if takeprofit is not None:
        hit = best >= takeprofit if long_side else best <= takeprofit
        if hit:
            return "自動止盈", False, _fill_price(takeprofit, open_price, long_side)

    return None, False, None


def _fill_price(level, open_price, long_side):
    """
    實際成交價。

    K 棒開盤就已經穿過觸發價時,不可能還在觸發價成交 —— 成交在開盤價。
    這是保守的一邊:假設還能在停損價出場會高估績效。
    """
    if open_price is None:
        return level

    if long_side and open_price < level:
        return open_price
    if not long_side and open_price > level:
        return open_price

    return level


def _intrabar_range(symbol):
    """
    輪詢間隔內的 (high, low, 第一根開盤價)。取不到就回 (None, None, None)。

    取不到不是錯誤 —— 呼叫端會退回單一價格判定。但它必須被記錄,
    因為那代表這一輪的停損判定比平常寬鬆。
    """
    if not settings.POSITION_MONITOR_USE_INTRABAR:
        return None, None, None

    try:
        from agmcis.data.market_data import get_ohlcv_dicts

        candles = get_ohlcv_dicts(
            symbol, timeframe="1m",
            limit=settings.POSITION_MONITOR_INTRABAR_CANDLES,
        )
    except Exception as exc:
        logger.warning(
            "Position Monitor | INTRABAR_FAILED | %s | %s | 本輪退回單一價格判定",
            symbol, exc,
        )
        return None, None, None

    if not candles:
        logger.warning(
            "Position Monitor | INTRABAR_EMPTY | %s | 本輪退回單一價格判定", symbol,
        )
        return None, None, None

    highs = [float(c["high"]) for c in candles if c.get("high") is not None]
    lows = [float(c["low"]) for c in candles if c.get("low") is not None]

    if not highs or not lows:
        return None, None, None

    first_open = candles[0].get("open")
    return max(highs), min(lows), (float(first_open) if first_open else None)


def _excursion(signal, entry_price, leverage, high, low, price):
    """
    這一輪的最大有利 / 不利偏移(%,已含槓桿)。

    用 K 棒的 high / low 而不是輪詢當下的價格 —— 兩次輪詢之間走到過
    的極端值才是 MFE / MAE 想量的東西。與停損判定用同一份資料,
    所以兩者不會互相矛盾(價格「碰到過」停損但 MAE 說沒有)。

    拿不到 K 棒時退回單一價格:量到的會比真實值保守
    (MFE 偏小、MAE 偏小),而保守的方向在這裡是安全的 ——
    它會讓「停損設得太緊」這個結論比較不容易成立,不會反過來。

    回傳 (有利%, 不利%)。算不出來回 (None, None) —— 不是 (0, 0),
    因為 0 代表「量過而且是零」。
    """
    try:
        entry = float(entry_price)
        lev = float(leverage or 1)
    except (TypeError, ValueError):
        return None, None

    if entry <= 0:
        return None, None

    best = high if high is not None else price
    worst = low if low is not None else price

    if is_long(signal):
        favourable = (float(best) - entry) / entry
        adverse = (float(worst) - entry) / entry
    elif is_short(signal):
        # 做空的有利方向是價格往下,所以 best 與 worst 對調。
        favourable = (entry - float(worst)) / entry
        adverse = (entry - float(best)) / entry
    else:
        return None, None

    # 有利不會是負的、不利不會是正的 —— 一根完全在進場價之上的
    # K 棒,對做多而言 MAE 是 0(從來沒有虧過),不是一個正數。
    return (
        round(max(favourable, 0.0) * lev * 100, 6),
        round(min(adverse, 0.0) * lev * 100, 6),
    )


def _track_excursion(trade, signal, high, low, price):
    """
    把這一輪的偏移寫進資料庫。

    **失敗不影響停損檢查。** 這是一個統計欄位,而停損是保護 ——
    一個因為寫不了統計而跳過停損檢查的監控,比沒有統計糟得多。
    """
    trade_id = trade.get("id")
    if trade_id is None:
        return

    favourable, adverse = _excursion(
        signal, trade.get("entry_price"), trade.get("leverage"),
        high, low, price,
    )
    if favourable is None:
        return

    try:
        from database_service import update_excursion
        update_excursion(trade_id, favourable, adverse)
    except Exception as exc:
        # migration 010 還沒跑的環境會走到這裡。不能安靜(第九十四節)。
        logger.warning(
            "Position Monitor | EXCURSION_WRITE_FAILED | %s | %s: %s",
            trade.get("symbol"), type(exc).__name__, exc,
        )


def run_position_monitor(notify=True):
    open_trades = get_open_trades()

    checked_symbols = []
    closed = []
    skipped = []
    unprotected = []

    for trade in open_trades:
        symbol = trade.get("symbol")
        signal = trade.get("signal")
        stoploss = trade.get("stoploss")
        takeprofit = trade.get("takeprofit")
        liquidation_price = trade.get("liquidation_price")

        if not (is_long(signal) or is_short(signal)):
            skipped.append({"symbol": symbol, "reason": f"方向無法辨識 ({signal!r})"})
            logger.error("Position Monitor | UNKNOWN_DIRECTION | %s | %r", symbol, signal)
            continue

        if stoploss is None:
            # 不是可以跳過的小事:這個倉位沒有風險上限。
            unprotected.append({"symbol": symbol, "signal": signal})
            logger.warning(
                "Position Monitor | NO_STOPLOSS | %s | %s | 該倉位沒有停損保護",
                symbol, signal,
            )

        price = get_price(symbol)

        if price is None:
            skipped.append({"symbol": symbol, "reason": "無法取得目前價格"})
            logger.warning("Position Monitor | NO_PRICE | %s | 本輪跳過停損檢查", symbol)
            continue

        price = float(price)

        # 輪詢間隔內真的走到過的價格。取不到就退回單一價格判定。
        high, low, open_price = _intrabar_range(symbol)

        checked_symbols.append({
            "symbol": symbol, "price": price,
            "high": high, "low": low,
            "intrabar": high is not None,
        })

        # MFE / MAE(第三十節 Agent 10)。在出場判定**之前**記 ——
        # 一筆這一輪被停損的交易,它的 MAE 也要含這一輪的極端值,
        # 否則「停損是不是設得太緊」那個問題會少掉最關鍵的一筆資料。
        _track_excursion(trade, signal, high, low, price)

        reason, liquidated, fill = _exit_reason(
            signal, price, stoploss, takeprofit, liquidation_price,
            high=high, low=low, open_price=open_price,
        )

        if not reason:
            continue

        if liquidated:
            # 強平在強平價成交,不是在輪詢到的那個價格成交,
            # 也不套用滑點(見 paper_trading.close_paper_trade)。
            logger.error(
                "Position Monitor | LIQUIDATION | %s | %s | 強平價=%s 當前價=%s",
                symbol, signal, liquidation_price, price,
            )
            result = close_paper_trade(
                symbol, fill, reason, liquidated=True,
            )
        else:
            result = close_paper_trade(symbol, fill, reason)

        if not result.get("success"):
            if result.get("already_closed"):
                logger.info("Position Monitor | ALREADY_CLOSED | %s", symbol)
            else:
                logger.error(
                    "Position Monitor | CLOSE_FAILED | %s | %s",
                    symbol, result.get("message"),
                )
            continue

        closed_trade = result["trade"]
        closed.append(closed_trade)

        logger.info(
            "Position Monitor | CLOSE | %s | %s | %s | 觸發價=%s 當前價=%s "
            "(盤中 high=%s low=%s) roi=%s%% pnl=%s",
            symbol, signal, reason, fill, price, high, low,
            closed_trade.get("pnl_pct"), closed_trade.get("pnl_usdt"),
        )

        if notify:
            notify_close_trade(
                symbol,
                signal,
                fill,
                pnl_pct=closed_trade.get("pnl_pct"),
                pnl_usdt=closed_trade.get("pnl_usdt"),
                reason=reason,
            )

    return {
        "checked": len(open_trades),
        "closed_count": len(closed),
        "closed": closed,
        "liquidated_count": len([c for c in closed if c.get("liquidated")]),
        # 這一輪有幾個標的是用盤中 high/low 判定的。
        # 數字比持倉數少代表有些標的取不到 K 棒,那一輪的停損判定比較寬鬆。
        "intrabar_checked": len([c for c in checked_symbols if c.get("intrabar")]),
        "checked_symbols": checked_symbols,
        "skipped": skipped,
        "unprotected": unprotected,
    }


if __name__ == "__main__":
    print(run_position_monitor())

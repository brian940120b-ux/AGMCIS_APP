"""
Paper Trading 引擎。

Phase 0.5 的修正:
  1. 開倉強制要有停損,且停損必須在正確的方向上(做多的停損低於進場價,做空的高於)。
     原本只要 float(stoploss) 不炸就通過 —— 停損放在錯邊會在下一秒立刻停損出場。
  2. 重複開倉改由資料庫 unique index 擋下,不再依賴 read-then-write 檢查。
  3. 平倉改走 close_trade_atomic():損益計算、交易更新、帳戶更新在同一 transaction 內,
     且損益乘上實際槓桿。回傳 None 代表沒有平到任何倉位(已被另一條路徑平掉),
     此時絕不調整餘額。
  4. 補上缺少的 logger import —— 原本 journal 寫入失敗時會拋 NameError 而不是記錄錯誤。

Phase 10 納入成本:

  * **成交價含點差與滑點**,而且永遠是不利的一邊。
    下單看到 100,做多實際成交在 100.06,平倉時再被扣一次。
  * **進出場手續費**各一次,依名目價值(保證金 × 槓桿)計算,不是依保證金。
  * **資金費用**依實際持倉時數累計。做多付、做空收(費率為正時)。
  * **強制平倉**:價格穿過強平價時以強平價出場,另收清算費,虧損上限是保證金。

成本模型與回測**共用同一個 CostModel**。兩邊假設不同,模擬盤就沒辦法
拿來驗證回測 —— 那正是模擬盤的用途。

歷史資料一律保留原值並標記 cost_basis = LEGACY_NO_COSTS,不回頭改寫。
"""
from database_service import (
    DuplicateOpenTradeError,
    close_trade_atomic,
    get_account,
    get_open_trade,
    get_open_trades,
    get_trades,
    insert_trade,
    update_account,
)
from agmcis.execution import paper_costs
from direction import is_directional, is_long, stop_loss_is_valid, take_profit_is_valid
from logger_service import logger


def load_account():
    return get_account()


def save_account(account):
    update_account(
        account["balance"],
        account["wins"],
        account["losses"],
        account["trades"],
    )


def load_trades():
    return get_trades()


def get_all_open_trades():
    return get_open_trades()


def has_open_trade(symbol):
    return get_open_trade(symbol) is not None


def _fail(message):
    logger.warning("Paper trade rejected | %s", message)
    return {"success": False, "message": message}


def create_paper_trade(
    symbol,
    entry_price,
    signal,
    size_usdt=1000,
    stoploss=None,
    takeprofit=None,
    leverage=1,
    position_value=None,
    source="MANUAL",
    agent_votes=None,
    market_regime=None,
    strategy=None,
    confidence=None,
):
    """
    agent_votes / market_regime / strategy / confidence 是 Phase 15 的歸因欄位。

    **必須在開倉當下記下來。** Agent 的投票取決於當下的指標,而指標會隨時間變 ——
    事後推不回來,那筆交易的歸因就永遠遺失了。
    """
    if not is_directional(signal):
        return _fail(f"{symbol} 方向無法辨識 ({signal!r}),拒絕開倉")

    try:
        entry_price = float(entry_price)
        size_usdt = float(size_usdt)
        leverage = float(leverage)
    except (TypeError, ValueError):
        return _fail(f"{symbol} 進場價 / 倉位 / 槓桿 數值異常,拒絕開倉")

    if entry_price <= 0:
        return _fail(f"{symbol} 進場價必須大於 0")

    if size_usdt <= 0:
        return _fail(f"{symbol} 倉位必須大於 0")

    if leverage <= 0:
        return _fail(f"{symbol} 槓桿必須大於 0")

    # 強制停損:沒有停損的倉位等於沒有風險上限,一律不允許開倉。
    if not stop_loss_is_valid(signal, entry_price, stoploss):
        return _fail(
            f"{symbol} 停損無效({signal} entry={entry_price} sl={stoploss})。"
            "開倉必須有停損,且做多停損須低於進場價、做空停損須高於進場價。"
        )

    # 停利允許不設,但設了就必須在正確方向。
    if takeprofit is not None and not take_profit_is_valid(signal, entry_price, takeprofit):
        return _fail(
            f"{symbol} 停利無效({signal} entry={entry_price} tp={takeprofit})。"
            "做多停利須高於進場價、做空停利須低於進場價。"
        )

    stoploss = float(stoploss)
    takeprofit = float(takeprofit) if takeprofit is not None else None

    # ---- Phase 10:成交價含成本 ----
    long_side = is_long(signal)
    # 費率依合約而異 —— 有校準過的規格就用那個標的的實際費率
    costs = paper_costs.get_cost_model(symbol)

    requested_entry_price = entry_price
    entry_price = paper_costs.fill_price(entry_price, long_side, is_entry=True,
                                         model=costs)

    if position_value is None:
        position_value = size_usdt * leverage

    entry_fee = costs.fee(position_value)
    # 名目價值要帶進去 —— 維持保證金率依倉位大小分層,
    # 用單一數字會低估大倉位的強平風險。
    liquidation_price = paper_costs.liquidation_price(
        entry_price, leverage, long_side, symbol=symbol,
        notional=position_value,
    )

    # 滑價之後停損可能已經在錯邊了 —— 那張單一開就會被停掉。
    # 這種情況下不開倉才是對的。
    if not stop_loss_is_valid(signal, entry_price, stoploss):
        return _fail(
            f"{symbol} 滑價後停損失效(下單價 {requested_entry_price} -> "
            f"成交價 {entry_price:.8f},停損 {stoploss})。停損距離太近,拒絕開倉。"
        )

    # 強平價比停損還近的倉位,實際上根本用不到停損。
    if liquidation_price is not None:
        if long_side and liquidation_price >= stoploss:
            return _fail(
                f"{symbol} 強平價 {liquidation_price:.8f} 比停損 {stoploss} 更接近進場價,"
                f"槓桿 {leverage}x 太高,拒絕開倉"
            )
        if not long_side and liquidation_price <= stoploss:
            return _fail(
                f"{symbol} 強平價 {liquidation_price:.8f} 比停損 {stoploss} 更接近進場價,"
                f"槓桿 {leverage}x 太高,拒絕開倉"
            )

    try:
        trade_id = insert_trade(
            symbol=symbol,
            signal=signal,
            entry_price=entry_price,
            size_usdt=size_usdt,
            stoploss=stoploss,
            takeprofit=takeprofit,
            leverage=leverage,
            position_value=position_value,
            source=source,
            requested_entry_price=requested_entry_price,
            entry_fee=entry_fee,
            liquidation_price=liquidation_price,
            cost_basis="WITH_COSTS",
            agent_votes=agent_votes,
            market_regime=market_regime,
            strategy=strategy,
            confidence=confidence,
        )
    except DuplicateOpenTradeError:
        return _fail(f"{symbol} 已有持倉,不重複開倉")

    try:
        from journal_service import log_open
        log_open(symbol, signal, entry_price, "Paper trade opened")
    except Exception as exc:
        logger.exception("Journal OPEN failed for %s: %s", symbol, exc)

    logger.info(
        "Paper trade OPEN | %s | %s | 下單=%s 成交=%.8f sl=%s tp=%s "
        "size=%s lev=%sx 強平=%s 進場費=%.4f source=%s",
        symbol, signal, requested_entry_price, entry_price, stoploss, takeprofit,
        size_usdt, leverage,
        f"{liquidation_price:.8f}" if liquidation_price else "n/a",
        entry_fee, source,
    )

    return {
        "success": True,
        "message": "模擬開倉成功",
        "trade": {
            "id": trade_id,
            "symbol": symbol,
            "signal": signal,
            "entry_price": entry_price,
            "requested_entry_price": requested_entry_price,
            "size_usdt": size_usdt,
            "position_value": position_value,
            "leverage": leverage,
            "stoploss": stoploss,
            "takeprofit": takeprofit,
            "liquidation_price": liquidation_price,
            "entry_fee": round(entry_fee, 6),
            "status": "OPEN",
        },
    }


def close_paper_trade(symbol, exit_price, close_reason="手動平倉",
                      liquidated=False, apply_slippage=True):
    """
    exit_price 是「看到的價格」。實際成交價會再被點差與滑點吃掉一層。

    liquidated=True 時另收清算費,而且**不套用滑點** ——
    強平是在強平價成交的,再加一次滑點會重複計算同一件事。
    """
    try:
        exit_price = float(exit_price)
    except (TypeError, ValueError):
        return _fail(f"{symbol} 出場價異常 ({exit_price!r}),拒絕平倉")

    if exit_price <= 0:
        return _fail(f"{symbol} 出場價必須大於 0")

    # 滑點在 close_trade_atomic 裡面算 —— 方向要從已鎖定的那一列讀,
    # 不能在這裡先查一次資料庫再拿去用。
    try:
        closed = close_trade_atomic(
            symbol, exit_price, close_reason,
            costs=paper_costs.get_cost_model(symbol),
            liquidated=liquidated,
            apply_slippage=apply_slippage,
        )
    except ValueError as exc:
        return _fail(f"{symbol} 平倉被拒:{exc}")

    if closed is None:
        # 不是錯誤,而是這次沒有平到任何倉位(通常代表另一條路徑已經平掉了)。
        logger.info("Paper trade CLOSE skipped | %s | 當下沒有 OPEN 倉位", symbol)
        return {
            "success": False,
            "message": f"{symbol} 沒有可平倉的持倉",
            "already_closed": True,
        }

    account = closed.pop("account")

    logger.info(
        "Paper trade CLOSE | %s | %s | 看到=%s 成交=%s roi=%s%% "
        "毛利=%s 費用=%s Funding=%s 淨損益=%s USDT | %s",
        symbol, closed["signal"], exit_price, closed.get("exit_price", exit_price),
        closed["pnl_pct"], closed.get("gross_pnl_usdt"),
        round((closed.get("entry_fee") or 0) + (closed.get("exit_fee") or 0), 6),
        closed.get("funding_usdt"), closed["pnl_usdt"], close_reason,
    )

    try:
        from journal_service import log_close
        log_close(
            symbol, closed["signal"], exit_price,
            closed["pnl_usdt"], closed["pnl_pct"], close_reason,
        )
    except Exception as exc:
        logger.exception("Journal CLOSE failed for %s: %s", symbol, exc)

    return {
        "success": True,
        "message": "模擬平倉成功",
        "trade": closed,
        "account": account,
    }


def get_paper_summary():
    account = load_account()
    trades = load_trades()

    open_trades = [t for t in trades if t["status"] == "OPEN"]
    closed_trades = [t for t in trades if t["status"] == "CLOSED"]

    total_trades = account["trades"]
    wins = account["wins"]
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

    costs = _cost_summary(closed_trades)

    return {
        "balance": round(account["balance"], 2),
        "wins": account["wins"],
        "losses": account["losses"],
        "trades": total_trades,
        "win_rate": round(win_rate, 2),
        "open_trades": open_trades,
        "closed_trades": closed_trades,
        "costs": costs,
    }


def _cost_summary(closed_trades):
    """
    成本總額。**只統計含成本的交易**(cost_basis = WITH_COSTS)。

    把不含成本的歷史資料混進來平均,「加了成本之後績效差多少」
    這個問題就永遠問不出答案 —— 與 pnl_basis 的處理方式一致。
    """
    with_costs = [t for t in closed_trades if t.get("cost_basis") == "WITH_COSTS"]
    legacy_count = len(closed_trades) - len(with_costs)

    fees = sum((t.get("entry_fee") or 0) + (t.get("exit_fee") or 0)
               for t in with_costs)
    funding = sum(t.get("funding_usdt") or 0 for t in with_costs)
    gross = sum(t.get("gross_pnl_usdt") or 0 for t in with_costs)
    net = sum(t.get("pnl_usdt") or 0 for t in with_costs)

    return {
        "trades_with_costs": len(with_costs),
        "legacy_trades_without_costs": legacy_count,
        "total_fees": round(fees, 4),
        "total_funding": round(funding, 4),
        "gross_pnl": round(gross, 4),
        "net_pnl": round(net, 4),
        "cost_drag": round(gross - net, 4),
        "liquidations": len([
            t for t in with_costs if t.get("close_reason") == "強制平倉"
        ]),
    }

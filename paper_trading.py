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

尚未納入(Phase 10 的範圍):手續費、滑點、Funding、強制平倉模擬。
目前的模擬損益是「不含成本」的上界,不要當成真實可達成的績效。
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
from direction import is_directional, stop_loss_is_valid, take_profit_is_valid
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
):
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
        )
    except DuplicateOpenTradeError:
        return _fail(f"{symbol} 已有持倉,不重複開倉")

    try:
        from journal_service import log_open
        log_open(symbol, signal, entry_price, "Paper trade opened")
    except Exception as exc:
        logger.exception("Journal OPEN failed for %s: %s", symbol, exc)

    logger.info(
        "Paper trade OPEN | %s | %s | entry=%s sl=%s tp=%s size=%s lev=%sx source=%s",
        symbol, signal, entry_price, stoploss, takeprofit, size_usdt, leverage, source,
    )

    return {
        "success": True,
        "message": "模擬開倉成功",
        "trade": {
            "id": trade_id,
            "symbol": symbol,
            "signal": signal,
            "entry_price": entry_price,
            "size_usdt": size_usdt,
            "leverage": leverage,
            "stoploss": stoploss,
            "takeprofit": takeprofit,
            "status": "OPEN",
        },
    }


def close_paper_trade(symbol, exit_price, close_reason="手動平倉"):
    try:
        exit_price = float(exit_price)
    except (TypeError, ValueError):
        return _fail(f"{symbol} 出場價異常 ({exit_price!r}),拒絕平倉")

    if exit_price <= 0:
        return _fail(f"{symbol} 出場價必須大於 0")

    try:
        closed = close_trade_atomic(symbol, exit_price, close_reason)
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
        "Paper trade CLOSE | %s | %s | exit=%s roi=%s%% pnl=%s USDT | %s",
        symbol, closed["signal"], exit_price,
        closed["pnl_pct"], closed["pnl_usdt"], close_reason,
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

    return {
        "balance": round(account["balance"], 2),
        "wins": account["wins"],
        "losses": account["losses"],
        "trades": total_trades,
        "win_rate": round(win_rate, 2),
        "open_trades": open_trades,
        "closed_trades": closed_trades,
    }

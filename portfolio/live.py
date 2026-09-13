"""
新系統 — 即時報價與盈虧 · 2026-09-08

═══ 為什麼要這一層 ═══
記帳是每日一次(訊號用收盤、成交在隔日開盤)—— 那是**策略的節奏**,
不會因為看盤而改變。但交易員要看的是**現在**手上這些倉賺賠多少,
而那個數字每一秒都在動。

兩者不衝突,前提是分清楚:
  · 記帳權益(daily)  決定策略、決定訂單、進契約與監控 —— 只用收盤
  · 即時權益(live)   只給人看,**不進任何決策、不寫任何帳本**

如果讓即時價格進到決策裡,策略就從「日線」變成「盯盤」,
而回測的 Calmar 1.33 是在日線規則下算出來的 —— 那會是第十二次「兩把尺」。
所以這一支只讀不寫,而且它算出來的任何數字都不落地。

═══ 快取 ═══
全市場行情一次呼叫 0.12 秒回 1030 個幣。快取 10 秒:
面板每 10 秒輪詢一次,交易所端每 10 秒最多被打一次。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import ratelimit
from core.logging import get_logger

log = get_logger("portfolio.live")

TTL_S = 10.0      # REST 退路的快取秒數(串流正常時用不到)
_CACHE: dict = {"t": 0.0, "px": {}, "err": None}


def prices(symbols: list[str] | None = None) -> dict[str, float]:
    """最新成交價。

    ═══ 兩層,順序不可顛倒 ═══
    一、WebSocket 串流(逐筆推播,實測約 10 筆/秒)—— 真正的即時
    二、REST 退路(10 秒快取)—— 只在串流還沒連上或斷線時用

    取不到就回空 dict。**絕不拿過期的價格假裝是即時價** ——
    一條靜止不動卻標著「即時」的價格,比沒有價格危險。
    """
    from portfolio import stream
    stream.start()
    px = stream.prices(symbols)
    if px and (symbols is None or len(px) == len(symbols)):
        return px

    # 串流尚未補齊 → REST 退路
    now = time.time()
    if now - _CACHE["t"] >= TTL_S or not _CACHE["px"]:
        try:
            from market_data.bingx_client import BingXClient
            out: dict[str, float] = {}
            for t in BingXClient().get_all_tickers():
                sym, last = t.get("symbol"), t.get("lastPrice")
                if not sym or last is None:
                    continue
                try:
                    out[sym] = float(last)
                except (TypeError, ValueError):
                    continue
            _CACHE.update({"t": now, "px": out, "err": None})
        except Exception as e:
            _CACHE.update({"t": now, "err": f"{type(e).__name__}: {e}"})
            log.warning(f"REST 退路取價失敗:{e}")
    rest = _CACHE["px"]
    merged = dict(rest) if symbols is None else {
        s: rest[s] for s in symbols if s in rest}
    merged.update(px)          # 串流的價格永遠優先
    return merged


_MARK: dict = {"t": 0.0, "px": {}, "err": None}
MARK_TTL_S = 5.0


def mark_prices(symbols: list[str] | None = None) -> dict[str, float]:
    """交易所的**標記價**(不是最新成交價)。

    ═══ 為什麼一定要分清楚這兩個價格 ═══
    交易所用標記價(mark price)判定強平與未實現盈虧,不用最新成交價。
    標記價來自指數價(多家現貨交易所的加權)加上基差的平滑,
    目的就是**擋掉單一交易所的插針**:有人在薄的盤口砸一筆把最新價
    打到你的強平價,標記價不會跟著跳,你的倉就不會被掃掉。

    CLAUDE.md 一直寫著「強平以標記價判定(用最新價會被插針掃掉)」,
    但 2026-09-09 排查發現實作餵進去的其實是**最新成交價** ——
    意圖是對的,實作沒跟上。平時兩者差 0.000%~0.008%(實測七幣),
    看起來無關緊要;但它們分岔的時刻,正好就是插針的時刻,
    也就是這個機制唯一會被用到的時刻。

    來源:/openApi/swap/v2/quote/premiumIndex(公開,不需金鑰)。
    不帶 symbol 一次回傳 1032 個交易對,所以一次抓全部再篩。
    快取 5 秒 —— 標記價本身是平滑後的值,不需要逐筆更新。
    """
    import json as _json
    import urllib.request
    now = time.time()
    if now - _MARK["t"] >= MARK_TTL_S or not _MARK["px"]:
        try:
            url = ("https://open-api.bingx.com"
                   "/openApi/swap/v2/quote/premiumIndex")
            with ratelimit.urlopen(url, timeout=8) as r:
                d = _json.loads(r.read().decode("utf-8"))
            if str(d.get("code")) != "0":
                raise RuntimeError(f"code={d.get('code')} {d.get('msg')}")
            out = {}
            for row in (d.get("data") or []):
                sym, mp = row.get("symbol"), row.get("markPrice")
                if not sym or mp is None:
                    continue
                try:
                    out[sym] = float(mp)
                except (TypeError, ValueError):
                    continue
            _MARK.update({"t": now, "px": out, "err": None})
        except Exception as e:
            _MARK.update({"t": now, "err": f"{type(e).__name__}: {e}"})
            log.warning(f"標記價取得失敗:{e}")
    px = _MARK["px"]
    return dict(px) if symbols is None else {
        s: px[s] for s in symbols if s in px}


def snapshot() -> dict:
    """即時帳戶快照。**只讀不寫** —— 不進任何決策,不落地任何檔案。"""
    from portfolio.account import Account
    a = Account.load()
    syms = sorted(a.positions)
    px = prices(syms)                 # 最新成交價(串流,逐筆)
    mkx = mark_prices(syms)           # 交易所標記價(REST,5 秒快取)
    stale = [s for s in syms if s not in px]

    rows = []
    upnl = 0.0
    for s in syms:
        p = a.positions[s]
        q = px.get(s)
        last = q if q is not None else p.avg_price
        # ── 風險數字一律用標記價,與交易所一致 ────────────────
        # 未實現盈虧、ROI、保證金率、強平距離,交易所全部用標記價算。
        # 標記價取不到時退回最新價,並在 mark_is_real 標明 —— 不假裝。
        mk = mkx.get(s)
        mark = mk if mk is not None else last
        u = p.unrealized(mark)
        upnl += u
        lp = p.liq_price()
        rows.append({
            "symbol": s, "position_amt": p.position_amt,
            "side": p.side, "avg_price": p.avg_price,
            # price 保留為「最新成交價」(面板逐筆跳動的那個數字),
            # mark_price 另立一欄 —— 就像交易所 App 兩個都顯示。
            "price": last, "live": q is not None,
            "mark_price": mark, "mark_is_real": mk is not None,
            "value": p.notional(mark), "upnl": u,
            "upnl_pct": ((mark / p.avg_price - 1) * 100 *
                         (1 if p.position_amt > 0 else -1))
            if p.avg_price else 0.0,
            "liq_price": lp,
            # 離強平還有多遠 —— 合約交易員第一個要看的風險數字
            "liq_dist_pct": (abs(mark - lp) / mark * 100)
            if (lp and mark) else None,
            "margin_ratio": p.margin_ratio(mark),
            "initial_margin": p.initial_margin(),
            # ROI 由 Position.roi() 算,跟帳本同一個函式 —— 不另寫一份
            "roi_pct": p.roi(mark),
            "leverage": p.leverage,
        })
    # 帳戶級數字(權益、保證金、強平檢查)也一律用標記價 —— 與交易所一致
    marks = {r["symbol"]: r["mark_price"] for r in rows}
    eq = a.equity(marks)
    return {
        "t": time.time(),
        "equity": eq,
        "balance": a.balance,
        "used_margin": a.used_margin(marks),
        "available_margin": a.available_margin(marks),
        "maint_margin": a.maint_margin(marks),
        "start_equity": a.start_equity,
        "return_pct": (eq / a.start_equity * 100 - 100) if a.start_equity else 0,
        # 交易所定義的已實現是淨額(平倉損益 − 手續費 − 資金費)。
        # 面板的 k-rp 欄顯示的就是這個,所以即時層必須送同一個定義,
        # 否則每 10 秒就會被毛額覆蓋回去 —— 那又是一次前後端兩把尺。
        "realized_pnl": a.realized_pnl - a.fee_paid - a.funding_paid,
        "realized_pnl_gross": a.realized_pnl,
        "unrealized_pnl": upnl,
        "exposure": a.exposure(marks),
        # 回撤以**記帳峰值**為基準:即時價不得改寫峰值,
        # 否則盤中一根上影線就會把峰值墊高,回撤看起來永遠很小。
        #
        # 但即時權益可以高於記帳峰值(峰值只在收盤更新),
        # 那會算出負回撤 —— 數學上正確,讀起來卻是錯的:
        # 回撤的定義是「從峰值跌下來多少」,不可能是負的。
        # 夾在 0,而峰值本身仍然只由記帳更新。
        "drawdown_pct": max(0.0, (a.peak_equity - eq) / a.peak_equity * 100)
        if a.peak_equity > 0 else 0.0,
        "liquidations": a.liquidation_check(marks),
        "positions": rows,
        "stale": stale,
        # 標記價取不到時要講出來 —— 那代表風險數字退回用最新價算的,
        # 而最新價正是會被插針掃到的那個價格。不能靜靜降級。
        "mark_stale": [s for s in syms if s not in mkx],
        "mark_error": _MARK.get("err"),
        "error": _CACHE.get("err"),
        "stream": _stream_state(),
    }


def _stream_state() -> dict:
    """串流健康狀態 —— 面板要能分辨「真即時」與「退路」。"""
    try:
        from portfolio import stream
        st = stream.state()
        return {"connected": bool(st.get("connected")),
                "msgs": int(st.get("msgs", 0)),
                "symbols": int(st.get("symbols", 0)),
                "uptime_s": round(float(st.get("uptime_s", 0)), 1),
                "error": st.get("error")}
    except Exception as e:
        return {"connected": False, "error": f"{type(e).__name__}: {e}"}

"""
新系統 — 紙上交易 · 2026-09-08

═══ 這一支的全部意義 ═══
50 日均線曝險 + 波動目標在 3.3 年歷史上兩段都贏過買入持有
(Calmar 1.33 vs 0.40),但最大回撤 17.4% 沒過畢業契約的 15% ——
**贏了基準不等於可交易**。一個看起來有料、卻沒過契約的東西,
該做的不是繼續在同一份歷史上挖,而是放到真實的、還沒發生過的價格上前向跑。

═══ 五層各司其職,這一支只負責串起來 ═══
    rules.py      訊號:收盤站上 50 日均線才持有
    rules.py      規模:波動目標 27%(回看 50 日,沿用訊號同一窗口)
    orders.py     訂單:買什麼、多少、出場線在哪
    execution.py  執行:紙上 / 實盤(實盤兩道硬鎖)
    account.py    帳本:數量、進場價、已實現/未實現損益
    monitor.py    監控:行為還在歷史範圍內嗎

═══ 鐵則一:與回測共用同一個規則函式 ═══
舊系統兩天內犯了十一次「兩把尺」——影子與法庭對同一件事各寫一份實作,
每一次都把負的算成正的。根治法只有一個:**不要有第二份實作。**
本支直接呼叫 portfolio.rules 與 portfolio.costs,一個常數都不自己寫。

═══ 鐵則二:訊號與成交之間永遠隔一個可交易的間隙 ═══
昨日收盤決定權重 → 今日開盤成交。與回測完全一致。

═══ 鐵則三:永不下單 ═══
本支沒有任何交易所 client 的 import。下單路徑在 execution.py,
而它的 LIVE_ENABLED 是原始碼常數,不是設定選項。
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.logging import get_logger
from portfolio import specs
from portfolio.account import Account
# costs 的估計值已不再用於記帳 —— 資金費改收 specs 提供的交易所實際結算值
from portfolio.execution import get_executor
from portfolio.orders import build_orders, format_orders
from portfolio.rules import _sma, registry, vol_target
from portfolio.sim import Bar

log = get_logger("portfolio.paper")

BASE = Path(__file__).resolve().parents[1]
CURVE = BASE / "data" / "portfolio_equity.jsonl"

# ── 上線設定:改動必須是刻意的、看得見的 ────────────────────
STRATEGY = "50日均線之上才持有"
SYMBOLS = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT",
           "XRP-USDT", "AAVE-USDT", "UNI-USDT"]
LOOKBACK_DAYS = 400                      # 夠算 200 日均線 + 緩衝
# 波動目標:由**訓練段**二分搜尋出「達成契約 15% 回撤」所需的值,
# 驗證段沒參與。上線後全段實測 Calmar 1.33 / 回撤 17.4%
#(原版 1.25 / 34.7%)—— 兩軸都優於原版,所以上線;
# 但 17.4% 仍未達契約 15%,而我沒有把目標再調低到剛好過:
# 那是拿驗證段的答案調參數。契約掛著沒過,由前向資料自己證明。
VOL_TARGET_ANNUAL_PCT = 27.0
VOL_LOOKBACK = 50                        # 沿用訊號同一個窗口,不新增參數
# 槓桿上限(2026-09-08 定 20.0 → **2026-09-10 改為 3.0**)。
#
# ═══ 為什麼改:20× 是零好處、純風險 ═══
# 2026-09-10 00:30 UNI-USDT 真的被強平了。UNI 從 6.748 跌到 6.155
# (−8.79%),而 20× 的強平距離只有 1/20 − 0.5% = **4.50%**。
#
# 我先前的分析錯在把兩件事混在一起:CLAUDE.md 寫「實際使用約 0.5×」
# 指的是**帳戶層曝險**(我們只拿 50% 名目,這是對的);但**每一倉的
# leverage 都被設成 20**,所以逐倉強平距離只有 4.5%。波動目標限制的是
# 「拿多少名目」,不是「每倉設幾倍槓桿」——單一幣跌 8.8% 就滅掉那一倉。
#
# 關鍵事實:**名目一樣 → 損益一樣**。槓桿只決定鎖多少保證金與強平距離:
#   20× 鎖 239(2.4%)強平距離 4.50%
#    3× 鎖 1,591(16.1%)強平距離 32.83%
# 而可用保證金有 9,616 —— 我們完全不缺保證金,20× 省下的那筆用不到。
#
# 3.0 這個數字有兩條獨立來源,不是搜出來的:
#   · 反推:強平距離需大於單日極端波動(本組合單日最大跌幅約 10~15%),
#     3× 給 32.8%,約 2~3 倍安全邊際
#   · 學術:arXiv 2102.04591 用 BitMEX 永續 2017-2021、43 萬筆 5 分鐘
#     資料 + 極值理論算出「多單建議 33% 保證金(3×)」以達成 1% 的
#     每日追繳機率;該文並指出常態分布假設會低估最適保證金至少 50%
#   · 業界:專業交易者方向性交易極少超過 5~10×,多數框架建議 3~5× 起步
LEVERAGE_CAP = 3.0
# ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Config:
    """一組跑法的完整設定。

    ═══ 為什麼要有這個(2026-09-10)═══
    執政官要把退役的放養組換成**測試組**:一個平行的紙上帳戶,跑不同
    設定,用同一段真實價格跟主城對照 —— 因為 09-09 我們連續四次撞到
    同一道牆:想測新東西只能用回測,而回測有後見之明偏誤;想改主系統
    又會讓前向樣本歸零(契約要 100 天,現在第 2 天)。

    **關鍵是不要有第二份實作。** 測試組不複製 paper.py,而是傳一個不同
    的 Config 進同一個 plan()/tick()。憲法鐵則一:舊系統兩天內犯了
    十一次「兩把尺」,每一次都把負的算成正的。參數化不是為了彈性,
    是為了讓兩條跑法**在結構上不可能分岔**。

    universe_fn:交易池怎麼決定。None = 用固定的 symbols(主城);
    測試組傳一個函式進來,每次記帳時重新篩。
    """
    name: str = "main"
    symbols: tuple[str, ...] = tuple(SYMBOLS)
    strategy: str = STRATEGY
    vol_target_pct: float = VOL_TARGET_ANNUAL_PCT
    vol_lookback: int = VOL_LOOKBACK
    leverage_cap: float = LEVERAGE_CAP
    lookback_days: int = LOOKBACK_DAYS
    state_path: Path = BASE / "data" / "portfolio_account.json"
    curve_path: Path = BASE / "data" / "portfolio_equity.jsonl"
    universe_fn: object = None          # () -> list[str] | None


MAIN = Config()


def _fresh_bars(symbols=None, lookback_days=None
                ) -> tuple[list[datetime], dict[str, dict]]:
    """抓最新日線。共用 market_data.history 的累積式快取。"""
    from market_data.history import load_or_download
    symbols = list(symbols or SYMBOLS)
    lookback_days = lookback_days or LOOKBACK_DAYS
    idx: dict[str, dict] = {}
    for s in symbols:
        try:
            ks = load_or_download(s, lookback_days, interval="1d")
        except Exception as e:
            log.warning(f"{s} 日線取得失敗,本輪跳過該幣:{e}")
            continue
        if len(ks) < 60:
            continue
        idx[s] = {k.open_time: Bar(k.open_time, k.open, k.high, k.low, k.close)
                  for k in ks}
    dates = sorted({t for v in idx.values() for t in v})
    return dates, idx


def _append(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def plan(now: datetime | None = None, cfg: "Config | None" = None) -> dict:
    """算出今日該下的單,**不執行**。面板與 Telegram 用這個預覽。

    cfg 預設是主城(MAIN),行為與參數化之前完全相同。
    """
    cfg = cfg or MAIN
    now = now or datetime.now(timezone.utc)
    # 交易池:主城是固定名單;測試組每次記帳重新篩(只用當下資訊)
    syms = list(cfg.universe_fn() if cfg.universe_fn else cfg.symbols)
    if not syms:
        return {"error": "交易池為空"}
    dates, idx = _fresh_bars(syms, cfg.lookback_days)
    if len(dates) < 60:
        return {"error": "日線資料不足"}
    i = len(dates) - 2                      # 訊號用最後一根完整日的收盤
    if i < 55:
        return {"error": "樣本不足以算 50 日均線"}
    # 訊號用第 i 日收盤,成交在第 i+1 日開盤 ——
    # 訊號與成交之間永遠隔一個可交易的間隙,與回測完全一致。
    day = dates[i]
    exec_day = dates[i + 1]

    fn = vol_target(registry(syms)[cfg.strategy], cfg.vol_target_pct,
                    cfg.vol_lookback, syms, cfg.leverage_cap)
    target = fn(i, dates, idx) or {}
    # 名目上限是 LEVERAGE_CAP,不是 1.0 —— vol_target 內部已經封頂在
    # leverage_cap,這裡只防禦性地擋掉萬一算出超過上限的情況。
    tot = sum(target.values())
    if tot > cfg.leverage_cap + 1e-9:
        target = {k: v * cfg.leverage_cap / tot for k, v in target.items()}

    prices = {s: idx[s][exec_day].o for s in idx if exec_day in idx[s]}
    mas = {s: m for s in syms
           if (m := _sma(idx, s, dates, i, cfg.vol_lookback)) is not None}

    a = Account.load(cfg.state_path)
    held_w = a.weights(prices)
    eq = a.equity(prices)
    # held_qty 讓全平倉用實際持有量下單,不靠權重反推(避免留下殘倉)
    held_qty = {s: p.position_amt for s, p in a.positions.items()}
    orders = build_orders(target, held_w, eq, prices, mas, held_qty)
    # 成交日的完整 K 棒 —— 強平要用當日最低/最高判定(盤中觸價就發生),
    # 只給開盤價會漏掉「盤中破線、收盤拉回」的情況,而那在真實交易所
    # 是實實在在被平掉了。
    bars_exec = {s: idx[s][exec_day] for s in idx if exec_day in idx[s]}
    return {"signal_day": day.isoformat(), "exec_day": exec_day.isoformat(),
            "target": target, "held": held_w, "equity": eq,
            "prices": prices, "bars_exec": bars_exec, "mas": mas,
            "orders": orders, "symbols": syms, "cfg": cfg,
            "already_done": a.last_signal_day == day.isoformat()}


def tick(now: datetime | None = None, cfg: "Config | None" = None) -> dict:
    """跑一日:收資金費 → 產訂單 → 執行 → 記帳。

    冪等:同一個訊號日重複呼叫不會重複記帳。
    cfg 預設是主城(MAIN),行為與參數化之前完全相同。
    """
    cfg = cfg or MAIN
    now = now or datetime.now(timezone.utc)
    p = plan(now, cfg)
    if "error" in p:
        return p
    a = Account.load(cfg.state_path)
    if p["already_done"]:
        return {"skipped": "本交易日已記帳",
                "equity": a.equity(p["prices"])}

    prices = p["prices"]
    if a.started_at is None:
        a.started_at = now.isoformat()
        a.bench_start = dict(prices)

    # ── 一、持倉先收資金費 ────────────────────────────
    # 2026-09-09:改成收**交易所實際結算過**的費率,不再用估計值。
    # 交易所每 8 小時結算一次(00/08/16 UTC),/quote/fundingRate 公布
    # 每一次真的收走的數字。原本用 gauge 抽樣的中位數,實測平均年化
    # 差 1.74%,UNI 甚至連正負號都相反(估計 +0.0033% 要付,實際
    # −0.0236% 是收錢)—— 而且方向是我們少收,前向績效被美化。
    # 只收 (上次收到的時點, 現在] 之間的結算,左開右閉:重跑不會重複收,
    # 跨日不會漏收。
    now_ms = int(now.timestamp() * 1000)
    since_ms = a.funding_through_ms or (now_ms - 24 * 3600 * 1000)
    daily_rates = {s: specs.funding_rate_sum(s, since_ms, now_ms)
                   for s in a.positions}
    fund = a.charge_funding(prices, daily_rates)
    a.funding_through_ms = now_ms

    # ── 一之二、Risk Engine 硬閘(2026-09-10 PHASE 1)──────────
    # Master Prompt 第 19 條:Risk Engine 是 HARD GATE,任何一層都不能繞過。
    # 在此之前風控只有波動目標(決定部位大小)與槓桿上限 —— 沒有任何一層
    # 在訂單產出**之後**、執行**之前**說「不行」。
    # 2026-09-10 的事故說明了為什麼需要:UNI 被強平,而六個倉全在懸崖邊
    # (BNB 距強平剩 0.14%),當時沒有任何規則會擋下那個狀態。
    #
    # **紙上與實盤共用同一個閘** —— 若只在實盤才檢查,紙上的績效就是在
    # 一個沒有風控的世界裡跑出來的,那是最貴的一種兩把尺。
    from portfolio.risk import RiskEngine
    curve_rows = []
    try:
        with cfg.curve_path.open(encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if ln:
                    curve_rows.append(json.loads(ln))
    except OSError:
        pass
    risk = RiskEngine().evaluate(a, p["orders"], prices, curve_rows)
    orders_in = list(p["orders"])
    if not risk.allowed:
        for c in risk.failures():
            log.warning(f"**風控否決** {c.name}:{c.detail}")
        orders_in = []
    elif risk.rejected_symbols:
        for c in risk.failures():
            log.warning(f"**風控拒單** {c.name}:{c.detail}")
        orders_in = [o for o in orders_in
                     if o.symbol not in risk.rejected_symbols]

    # ── 二、執行今日訂單 ─────────────────────────────
    ex = get_executor()
    filled = ex.submit(orders_in, now)
    realized = 0.0
    for rec in filled:
        # PARTIAL 也要記帳 —— 部分成交是**真的成交了**,只是量比較少。
        # 只認 FILLED 會讓部分成交的部位完全不進帳本,而交易所那邊
        # 卻真的有倉:帳本與交易所從此對不起來,且沒有地方會發現。
        if rec.get("status") not in ("FILLED", "PARTIAL"):
            continue
        # 記**實際成交量**,不是下單量(見 execution.submit 的說明)
        qty = float(rec.get("filled_qty", rec["qty"]))
        if qty <= 0:
            continue
        # 手續費 = 成交名目 × **該幣的**吃單費率(交易所逐幣給,不是全域常數)。
        # 2026-09-09 修:原本用 round_trip_pct()/2,那個數字把滑點也算進來,
        # 等於把「成交價變差」記成「被收了一筆錢」。滑點已經改成由執行層
        # 調整成交價(見 execution.PaperExecutor._place),這裡只收真正的手續費。
        # 名目也改用**實際成交價**算,不是下單時的參考價。
        # 名目一律由「實際成交量 × 實際成交價」現算,不讀 rec["notional"] ——
        # 部分成交時那個欄位是下單量算的,直接拿來會多收手續費。
        px = float(rec["price"])
        fee = qty * px * specs.taker_fee_pct(rec["symbol"]) / 100.0
        realized += a.fill(rec["symbol"], rec["side"], qty, px, fee,
                           now.isoformat(), leverage=cfg.leverage_cap)

    # ── 二之二、強平:偵測**並執行** ──────────────────────
    # 2026-09-10 修:原本只寫一行 log,部位照樣留著繼續虧。當天 00:30
    # UNI-USDT 真的觸發了,而帳本讓它虧到 −61.67(投入保證金只有 35.09,
    # ROI −175%)。真實交易所在強平價就把它平了,損失止於保證金 ——
    # 從那一刻起帳本與現實永久分岔,而且價格反彈時帳本還會「賺回來」,
    # 那在現實中不可能發生。
    #
    # 判定用**當日最低/最高價**而不是只用開盤價:強平是盤中觸價就發生的,
    # 只看開盤會漏掉「盤中破線、收盤又拉回」的情況 —— 而那種情況在真實
    # 交易所是實實在在被平掉了。
    intraday = {}
    for s in list(a.positions):
        bar = p["bars_exec"].get(s)
        if bar is None:
            continue
        intraday[s] = bar.l if a.positions[s].position_amt > 0 else bar.h
    liquidated = []
    for s in a.liquidation_check(intraday):
        liquidated.append(a.liquidate(s, now.isoformat()))
    if liquidated:
        log.warning(f"**強平執行** {len(liquidated)} 倉:"
                    + ", ".join(x["symbol"] for x in liquidated))

    # ── 三、結帳 ────────────────────────────────────
    eq = a.equity(prices)
    a.peak_equity = max(a.peak_equity, eq)
    a.days += 1
    a.last_signal_day = p["signal_day"]
    a.updated = now.isoformat()
    a.strategy = f"{cfg.strategy} + 波動目標 {cfg.vol_target_pct}%"
    a.save(prices, cfg.state_path)

    # 基準:同日開始、等權買入持有(也是永續,也付資金費)
    #
    # 2026-09-09:基準的資金費也改用**交易所實際結算值**。
    # 組合這一側已經改成收實際費率,基準若還用估計值,就是拿兩把不同的
    # 尺在比 —— 而「勝過基準」是實盤資格契約的第三條,比較有偏差
    # 等於契約在用錯的證據判決。基準等權持有全部標的,所以取逐幣
    # 實際結算合計的平均。
    bench = None
    if a.bench_start:
        legs = [(prices[s] / p0 - 1) for s, p0 in a.bench_start.items()
                if s in prices and p0 > 0]
        if legs:
            bench = sum(legs) / len(legs) * 100
            if a.started_at:
                start_ms = int(datetime.fromisoformat(
                    a.started_at).timestamp() * 1000)
                held = [s for s in a.bench_start if s in prices]
                if held:
                    fees = [specs.funding_rate_sum(s, start_ms, now_ms) * 100
                            for s in held]
                    bench -= sum(fees) / len(fees)

    dd = ((a.peak_equity - eq) / a.peak_equity * 100) if a.peak_equity else 0.0
    _append(cfg.curve_path, {
        "t": now.isoformat(), "signal_day": p["signal_day"],
        "equity": round(eq, 4),
        "return_pct": round(eq / a.start_equity * 100 - 100, 4),
        "benchmark_pct": round(bench, 4) if bench is not None else None,
        "drawdown_pct": round(dd, 4),
        "exposure": round(a.exposure(prices), 4),
        "realized_pnl": round(a.realized_pnl, 4),
        "unrealized_pnl": round(a.unrealized(prices), 4),
        "balance": round(a.balance, 4),
        "used_margin": round(a.used_margin(prices), 4),
        "available_margin": round(a.available_margin(prices), 4),
        "holdings": sorted(a.positions),
        "orders": len(filled), "funding": round(fund, 4),
        # 強平是重大事件,必須落進權益曲線 —— 只留在 log 等於沒人會看到
        "liquidated": [x["symbol"] for x in liquidated] or None,
        # 風控否決是重大事件 —— 只留在 log 等於沒人會看到
        "risk_verdict": risk.verdict,
        "risk_rejected": sorted(risk.rejected_symbols) or None,
        "risk_failures": [c.name for c in risk.failures()] or None})

    if cfg.name == "main":
        try:
            from portfolio.monitor import analyze as monitor_analyze
            monitor_analyze()
        except Exception as e:
            log.warning(f"監控更新失敗(不影響記帳):{e}")

    return {"liquidated": liquidated, "risk": risk.to_dict(),
            "equity": eq, "return_pct": eq / a.start_equity * 100 - 100,
            "benchmark_pct": bench, "drawdown_pct": dd,
            "holdings": sorted(a.positions), "orders": filled,
            "realized_pnl": a.realized_pnl,
            "unrealized_pnl": a.unrealized(prices),
            "venue": ex.venue, "realized_today": realized}


def main() -> int:
    r = tick()
    if "error" in r:
        print(f"無法記帳:{r['error']}")
        return 1
    if "skipped" in r:
        print(f"{r['skipped']} · 權益 {r['equity']:,.2f}")
        return 0
    b = r.get("benchmark_pct")
    print(f"[{r['venue']}] 權益 {r['equity']:,.2f}({r['return_pct']:+.2f}%)"
          f" · 基準 {f'{b:+.2f}%' if b is not None else '—'}"
          f" · 回撤 {r['drawdown_pct']:.2f}%")
    print(f"已實現 {r['realized_pnl']:+.2f} · 未實現 {r['unrealized_pnl']:+.2f}"
          f" · 持有 {len(r['holdings'])} 檔")
    if r["orders"]:
        print(f"\n今日成交 {len(r['orders'])} 單:")
        for o in r["orders"]:
            print(f"  {o['side']:<4} {o['symbol']:<11} {o['qty']:.6g} "
                  f"@ {o['price']:,.6g}  {o.get('reason', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

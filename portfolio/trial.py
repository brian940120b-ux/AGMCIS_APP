"""
測試組 — 平行紙上帳戶 · 2026-09-10

═══ 為什麼要有這一組 ═══
2026-09-09 我們連續四次撞到同一道牆:
  想測新東西 → 只能用回測 → 回測有後見之明偏誤 → 判斷不了
  想改主系統 → 前向樣本歸零 → 契約 100 天重新開始

最明顯的例子是**動態交易池**:測完之後只能誠實說「回測沒辦法判斷,
因為固定 7 幣的數字被後見之明美化了」(見 docs/result-dynamic-universe
-and-xsmom.md)。那個問題**只有前向測試能回答**。

測試組就是為此存在:一個平行的紙上帳戶,跑不同設定,用**同一段真實
價格、同一個時間**跟主城對照 —— 沒有後見之明,也不動主城的樣本。

執政官 2026-09-10 裁定:退役放養組(它的問題已有答案:Sharpe −1.59,
拿掉規則沒有產生優勢),改建測試組銜接「以盈利為目標」的方向。

═══ 三個鐵則 ═══
一、**共用同一份策略程式碼。** 本檔不複製 paper.py 的任何邏輯,只組一個
    不同的 Config 傳進同一個 plan()/tick()。憲法鐵則一:舊系統兩天內
    犯了十一次「兩把尺」,每一次都把負的算成正的。
    tests/test_portfolio_parity.py 斷言 paper.py 裡只能有一個 plan()
    與一個 tick()。
二、**對主城唯讀。** 自己的帳本、自己的權益曲線。絕不寫入主城任何檔案。
三、**預先登記採用判準**(見下),否則測試組會變成「哪個好看用哪個」——
    那是過度優化換一個形式。

═══ 第一個要測的:動態交易池 ═══
每次記帳重新篩選,只用**當下**就知道的資訊:
  · 過去 30 日成交額中位數 ≥ 1,000 萬 USDT(成本模型成立的前提)
  · 有 ≥60 根日線(算得出 50 日均線)
  · 交易所狀態可交易
  · 排除代幣化傳統資產(NCFX 外匯 / NCCO 商品 / NCSI 股指 / NCSK 個股)
    —— 09-09 實測:928 個候選裡有 362 個是這類,而且流動性極好
    (黃金/日圓成交額比 BTC 還大)。它們的年化波動約 10%,而波動目標
    看到低波動會大幅加碼,等於用高槓桿做外匯。
無任何預測性條件(不排名、不挑漲最多的)—— 橫斷面動能已於 09-09
預先登記測試並失敗(三條判準全滅),不再重測。

═══ 採用判準(預先登記,不得事後放寬)═══
測試組要取代主城設定,必須**三條全過**:
  一、前向樣本 ≥ 60 個交易日
  二、Calmar 勝過主城
  三、最大回撤不高於主城
三條全過也只是「有資格談」—— 換不換由執政官裁決,永不自動切換。
"""
from __future__ import annotations

import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.logging import get_logger

log = get_logger("portfolio.trial")

BASE = Path(__file__).resolve().parents[1]
STATE = BASE / "data" / "trial_account.json"
CURVE = BASE / "data" / "trial_equity.jsonl"

# ── 動態交易池的篩選門檻(與 09-09 的實驗同值,不另訂數字)──────
MIN_DOLLAR_VOL = 1e7        # 過去 30 日成交額中位數
VOL_WINDOW = 30
MIN_BARS = 60               # 算得出 50 日均線
MAX_UNIVERSE = 25           # 上限:避免池子暴增讓每倉小到被最小下單量擋掉
# 代幣化傳統資產 —— 不是加密貨幣,波動結構完全不同
NON_CRYPTO = ("NCFX", "NCCO", "NCSI", "NCSK")
# ─────────────────────────────────────────────────────────

# ── 採用判準(預先登記 2026-09-10,不得事後放寬)────────────────
ADOPT_MIN_DAYS = 60
# ─────────────────────────────────────────────────────────


def screen() -> list[str]:
    """動態交易池:只用**當下**就知道的資訊篩選。

    前向運作天然沒有前視偏誤 —— 我們本來就只能看到今天以前的資料。
    這正是測試組比回測可信的地方。
    """
    import csv

    from portfolio import specs

    hist = BASE / "data" / "history"
    try:
        import urllib.request
        import json as _json
        req = urllib.request.Request(
            "https://open-api.bingx.com/openApi/swap/v2/quote/ticker",
            headers={"User-Agent": "agmcis/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            tick = _json.loads(r.read())["data"]
    except Exception as e:
        log.warning(f"行情取得失敗,交易池沿用既有帳本持倉:{e}")
        return _held_symbols()

    cand = []
    for t in tick:
        sym = t.get("symbol", "")
        if sym.startswith(NON_CRYPTO):
            continue
        try:
            if float(t["quoteVolume"]) < MIN_DOLLAR_VOL:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        try:
            if not specs.tradable(sym):
                continue
        except specs.SpecMissing:
            continue
        cand.append((sym, float(t["quoteVolume"])))

    # 歷史長度:用已落地的日線快取判斷,不逐幣打 API(那會慢到不可用)
    out = []
    for sym, qv in sorted(cand, key=lambda x: -x[1]):
        f = hist / f"{sym}_1d.csv"
        if not f.exists():
            continue
        try:
            with f.open(encoding="utf-8") as fh:
                n = sum(1 for _ in csv.DictReader(fh))
        except OSError:
            continue
        if n >= MIN_BARS:
            out.append(sym)
        if len(out) >= MAX_UNIVERSE:
            break

    if not out:
        log.warning("動態篩選結果為空,沿用既有帳本持倉")
        return _held_symbols()
    log.info(f"動態交易池 {len(out)} 幣:{', '.join(out[:8])}…")
    return out


def _held_symbols() -> list[str]:
    """退路:篩不出來時至少維持既有持倉,不要無聲清倉。"""
    from portfolio.account import Account
    return sorted(Account.load(STATE).positions) or []


def bootstrap_history() -> int:
    """把候選幣的日線抓下來 —— 動態池要換幣時得有資料可算均線。

    每日跑一次,抓成交額前 MAX_UNIVERSE 名(含目前持倉)。
    """
    from market_data.history import load_or_download
    from portfolio.paper import LOOKBACK_DAYS

    want = set(screen()) | set(_held_symbols())
    ok = 0
    for s in sorted(want):
        try:
            load_or_download(s, LOOKBACK_DAYS, interval="1d")
            ok += 1
        except Exception as e:
            log.warning(f"{s} 日線取得失敗:{e}")
    return ok


def config():
    """測試組的設定 —— 只有交易池與帳本路徑跟主城不同。

    策略、波動目標、槓桿、成本一律沿用主城,**唯一的變因是交易池**。
    一次只改一個東西,否則贏了也不知道是誰帶來的。
    """
    from portfolio.paper import MAIN, Config
    return Config(
        name="trial",
        symbols=MAIN.symbols,          # 篩不出來時的退路
        strategy=MAIN.strategy,
        vol_target_pct=MAIN.vol_target_pct,
        vol_lookback=MAIN.vol_lookback,
        leverage_cap=MAIN.leverage_cap,
        lookback_days=MAIN.lookback_days,
        state_path=STATE,
        curve_path=CURVE,
        universe_fn=screen,
    )


def tick(now: datetime | None = None) -> dict:
    """跑測試組一日。共用 paper.tick(),只是換一個 Config。"""
    from portfolio.paper import tick as paper_tick
    return paper_tick(now or datetime.now(timezone.utc), config())


def _curve(path: Path) -> list[dict]:
    import json as _json
    out = []
    try:
        with path.open(encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if ln:
                    out.append(_json.loads(ln))
    except OSError:
        pass
    return out


def compare() -> dict:
    """測試組 vs 主城 —— 對照同一段前向期間,並套用預先登記的採用判準。"""
    from portfolio.paper import MAIN

    def metrics(rows):
        if len(rows) < 2:
            return None
        eq = [float(r["equity"]) for r in rows]
        peak, mdd = eq[0], 0.0
        for v in eq:
            peak = max(peak, v)
            mdd = max(mdd, (peak - v) / peak)
        years = max(len(eq) / 365.0, 1e-9)
        cagr = (eq[-1] / eq[0]) ** (1 / years) - 1
        rets = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq))]
        sd = statistics.pstdev(rets) if len(rets) > 1 else 0.0
        return {"days": len(eq),
                "total_pct": (eq[-1] / eq[0] - 1) * 100,
                "cagr_pct": cagr * 100,
                "max_dd_pct": mdd * 100,
                "calmar": (cagr / mdd) if mdd > 1e-9 else 0.0,
                "sharpe": (statistics.mean(rets) / sd * (365 ** 0.5))
                if sd > 0 else 0.0}

    m = metrics(_curve(MAIN.curve_path))
    t = metrics(_curve(CURVE))
    out = {"updated": datetime.now(timezone.utc).isoformat(),
           "main": m, "trial": t,
           "adopt_min_days": ADOPT_MIN_DAYS,
           "criteria": [], "eligible": False}
    if not m or not t:
        out["verdict"] = "樣本不足,尚無法比較。"
        return out

    c1 = t["days"] >= ADOPT_MIN_DAYS
    c2 = t["calmar"] > m["calmar"]
    c3 = t["max_dd_pct"] <= m["max_dd_pct"]
    out["criteria"] = [
        {"name": f"前向 ≥{ADOPT_MIN_DAYS} 天", "passed": c1,
         "detail": f"{t['days']}/{ADOPT_MIN_DAYS} 天"},
        {"name": "Calmar 勝過主城", "passed": c2,
         "detail": f"{t['calmar']:.2f} vs {m['calmar']:.2f}"},
        {"name": "回撤不高於主城", "passed": c3,
         "detail": f"{t['max_dd_pct']:.2f}% vs {m['max_dd_pct']:.2f}%"},
    ]
    out["eligible"] = c1 and c2 and c3
    out["verdict"] = ("三條全過 —— 有資格談替換,仍需執政官裁決。"
                      if out["eligible"] else
                      "尚未達標。測試組只累積證據,永不自動切換設定。")
    return out


def main() -> int:
    print("測試組:更新候選幣日線")
    print(f"  {bootstrap_history()} 個幣的日線就緒")
    print("測試組:記帳")
    r = tick()
    print(f"  {r.get('skipped') or r.get('error') or f'''權益 {r['equity']:,.2f}'''}")
    c = compare()
    print(f"\n{c['verdict']}")
    for x in c["criteria"]:
        print(f"  {'✓' if x['passed'] else '✗'} {x['name']}:{x['detail']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

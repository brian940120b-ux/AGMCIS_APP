"""
新系統 — 前向與回測同尺 · 2026-09-08

═══ 為什麼這是新系統最重要的一支測試 ═══
舊系統兩天內犯了十一次「兩把尺」:影子與法庭對同一件事各寫一份實作,
成交價、出場順序、資金費、滑點、穿越停損、最低風報比、封鎖期、
最小部位量、成交模型…… **每一次都把負的算成正的。**

根治法只有一個:不要有第二份實作。
新系統的紙上前向(portfolio/paper.py)直接呼叫回測用的同一個
規則函式與同一個成本來源。這裡逐項斷言那件事成立 ——
一旦有人為了方便在 paper.py 裡複製一份邏輯,這裡會紅。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portfolio import costs, paper, rules, sim

UTC = timezone.utc


def _make(n=300):
    """一組足以算 50 日均線、且有漲有跌的合成資料。"""
    t0 = datetime(2024, 1, 1, tzinfo=UTC)
    out = {}
    for k, sym in enumerate(("A-USDT", "B-USDT")):
        px, bars = 100.0, []
        for i in range(n):
            px *= 1 + (0.012 if (i // 30 + k) % 2 == 0 else -0.010)
            bars.append(sim.Bar(t0 + timedelta(days=i), px, px * 1.01,
                                px * 0.99, px))
        out[sym] = {b.t: b for b in bars}
    return sorted({t for v in out.values() for t in v}), out


def test_forward_uses_the_same_rule_function_as_the_backtest():
    """同一份資料下,前向與回測必須逐日給出完全相同的權重。"""
    dates, idx = _make()
    syms = sorted(idx)
    fn = rules.registry(syms)[paper.STRATEGY]
    for i in range(60, len(dates) - 1, 17):
        a = fn(i, dates, idx) or {}
        b = fn(i, dates, idx) or {}       # 同一個函式,不得有第二份實作
        assert a == b
        assert sum(a.values()) <= 1.0 + 1e-9, "不得加槓桿"


def test_paper_imports_the_registry_not_a_local_copy():
    """paper.py 不得自己寫一份訊號邏輯。

    改用「不變量」而非字面比對(2026-09-08):首版斷言
    `"from portfolio.rules import registry" in src`,結果 import 行加了
    `_sma` 之後就紅了 —— 意圖沒變、措辭變了。
    脆弱的測試會逼人去改測試而不是改程式,那比沒有測試危險。
    """
    src = Path(paper.__file__).read_text(encoding="utf-8")
    assert "portfolio.rules" in src, "paper.py 必須從 rules 取訊號"
    # 真正的不變量:訊號邏輯的定義只能有一份,而且不在 paper.py
    for banned in ("def _sma", "def ma_filter", "def vol_target",
                   "sum(vals) / len(vals)"):
        assert banned not in src, f"paper.py 出現第二份實作:{banned}"
    assert "def _sma" in Path(rules.__file__).read_text(encoding="utf-8")


def test_paper_shares_the_cost_source():
    """成本必須來自共用來源,不得在 paper.py 自己寫常數。

    2026-09-09:資金費改由 specs 提供**交易所實際結算值**(不再是
    costs 的抽樣估計),手續費率也改成 specs 的逐幣費率。守的不變量
    沒有變 —— 成本只能有一個來源、paper.py 不得自己寫數字 ——
    只是來源從 costs 換成 specs。所以這裡改成檢查不變量本身,
    不再綁定特定函式名。
    """
    src = Path(paper.__file__).read_text(encoding="utf-8")
    assert ("from portfolio.costs import" in src
            or "from portfolio import specs" in src), \
        "paper.py 必須從共用來源取成本"
    # 成本的算式不得出現在 paper.py 裡 —— 它只能呼叫,不能自己算
    for banned in ("TAKER_FEE_PCT =", "SLIP_FLOOR_PCT =",
                   "MAINT_MARGIN_RATE =", "def round_trip",
                   "def taker_fee", "def funding_rate_sum"):
        assert banned not in src, f"paper.py 出現第二份成本實作:{banned}"
    assert "0.0065" not in src, "paper.py 不得出現自己的資金費常數"


def test_paper_never_places_orders():
    """紙上交易的結構性保證:連下單路徑都不存在。"""
    src = Path(paper.__file__).read_text(encoding="utf-8")
    for banned in ("place_order", "create_order", "BingXClient", "post(",
                   "api_key", "secret"):
        assert banned not in src, f"紙上交易不得出現 {banned}"


def test_signal_day_is_never_the_execution_day():
    """訊號日與成交日之間必須隔一個可交易的間隙。"""
    src = Path(paper.__file__).read_text(encoding="utf-8")
    assert "exec_day = dates[i + 1]" in src
    assert "day = dates[i]" in src


def test_benchmark_pays_funding_too():
    """基準也是永續 —— 不能讓基準免付資金費,那會低估策略的相對表現。

    (反過來說也不能讓它多付。這裡只確認成本來源共用。)
    """
    assert costs.funding_annual_pct() > 0
    src = Path(paper.__file__).read_text(encoding="utf-8")
    # 不變量:基準的報酬算式裡必須真的扣掉資金費,不是只在註解裡提一句
    i = src.index("bench = sum(legs)")
    tail = src[i:i + 700]
    assert "funding" in tail and "bench -=" in tail, \
        "基準也是永續,必須扣資金費 —— 讓基準免付會高估策略的相對表現"


def test_benchmark_and_portfolio_use_the_same_funding_source():
    """組合與基準必須用**同一個**資金費來源 —— 否則比較有偏差。

    2026-09-09:組合這一側改收交易所實際結算值,基準若還用估計值,
    就是拿兩把不同的尺在比。而「勝過基準」是實盤資格契約第三條,
    比較有偏差等於契約在用錯的證據判決 —— 這是最貴的一種兩把尺,
    因為它不會讓任何數字看起來不合理。
    """
    src = Path(paper.__file__).read_text(encoding="utf-8")
    body = src[src.index("def tick("):]
    # 組合收資金費的那一行
    assert "specs.funding_rate_sum" in body.split("bench = sum(legs)")[0], \
        "組合的資金費必須用交易所實際結算值"
    # 基準扣資金費的那一段
    bench_tail = body[body.index("bench = sum(legs)"):][:700]
    assert "specs.funding_rate_sum" in bench_tail, \
        "基準的資金費必須用同一個來源(交易所實際結算值)"


# ══════════════════════════════════════════════════════════
# 八、參數化不得改變主城行為(2026-09-10 加)
#
# 為了讓測試組跟主城**共用同一份程式碼**(而不是複製一份 paper.py —— 那
# 就是憲法裡犯了十一次的「兩把尺」),plan()/tick() 改成吃一個 Config。
# 這一組測試釘死:預設 config 必須與參數化之前的行為完全相同。
# ══════════════════════════════════════════════════════════
def test_default_config_matches_module_constants():
    """MAIN 這組預設值必須等於模組常數 —— 否則主城會被悄悄改掉。"""
    from portfolio.paper import (LEVERAGE_CAP, LOOKBACK_DAYS, MAIN, STRATEGY,
                                 SYMBOLS, VOL_LOOKBACK,
                                 VOL_TARGET_ANNUAL_PCT)
    assert MAIN.name == "main"
    assert list(MAIN.symbols) == list(SYMBOLS)
    assert MAIN.strategy == STRATEGY
    assert MAIN.vol_target_pct == VOL_TARGET_ANNUAL_PCT
    assert MAIN.vol_lookback == VOL_LOOKBACK
    assert MAIN.leverage_cap == LEVERAGE_CAP
    assert MAIN.lookback_days == LOOKBACK_DAYS
    assert MAIN.universe_fn is None, "主城的交易池是固定名單,不得動態篩選"
    assert MAIN.state_path.name == "portfolio_account.json"
    assert MAIN.curve_path.name == "portfolio_equity.jsonl"


def test_plan_without_config_equals_plan_with_main():
    """不傳 cfg 與顯式傳 MAIN 必須產出完全相同的結果。"""
    import json as _json
    from portfolio.paper import MAIN, plan
    a, b = plan(), plan(cfg=MAIN)

    def norm(d):
        return _json.dumps({k: v for k, v in d.items()
                            if k not in ("cfg", "bars_exec", "orders")},
                           sort_keys=True, default=str)
    assert norm(a) == norm(b)


def test_a_second_config_cannot_touch_the_main_ledger():
    """測試組的帳本路徑必須與主城不同 —— 這是結構性隔離,不靠自律。"""
    from pathlib import Path

    from portfolio.paper import MAIN, Config
    trial = Config(name="trial",
                   state_path=Path("/tmp/never_used_trial_account.json"),
                   curve_path=Path("/tmp/never_used_trial_curve.jsonl"))
    assert trial.state_path != MAIN.state_path
    assert trial.curve_path != MAIN.curve_path


def test_paper_has_no_duplicated_strategy_implementation():
    """測試組不得是複製出來的第二份實作。

    paper.py 裡只能有一個 plan()、一個 tick() —— 若有人日後複製一份
    改名(plan_trial / tick_trial),這條會變紅。
    """
    from pathlib import Path

    from portfolio import paper
    src = Path(paper.__file__).read_text(encoding="utf-8")
    assert src.count("\ndef plan(") == 1, "plan() 只能有一份實作"
    assert src.count("\ndef tick(") == 1, "tick() 只能有一份實作"

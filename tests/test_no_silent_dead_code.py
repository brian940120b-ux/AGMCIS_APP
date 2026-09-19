"""
沒有人呼叫的東西,必須是**被審視過**的 · 2026-09-19

執政官:「去反查看有沒有哪裡不對。」

反查用的是一支掃描腳本:找出「定義了公開函式,而整個倉庫(排除
tests/)沒有任何地方呼叫它」。第一次跑出 **42 個**。

裡面藏著這一整個 session 最重要的一個發現:

    trade.unprotected() —— docstring 自稱「實盤最重要的一條巡檢」,
    而沒有任何地方呼叫它。

它跟 catch_up、ma_long_short、WebSocket 串流、price_in_band 是同一件事:

    **一個看起來存在、實際無作用的東西,比沒有更糟。**
    沒有的東西,人知道自己沒有。有而不通的東西,人以為自己有了。

═══ 這一組不要求「全部接上」═══
有些東西本來就該躺著:抽象基底類別的方法、框架覆寫(do_GET)、
動態註冊的巡檢函式、還沒開的實盤路徑。

要求的是**每一個都被看過一次,並且寫下理由**。
清單長了就會紅,而紅的時候唯一的處置是:接上它、刪掉它,
或在下面寫一行「為什麼它可以躺著」。

那一行的作用不是文件,是**強迫有人回頭問一次**:
這東西現在還該躺著嗎。
"""
from __future__ import annotations

import ast
import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SKIP_DIRS = {".git", "__pycache__", "tests", "data", ".venv"}

#: 已審視、可以躺著的 —— 每一個都要有理由。
DORMANT = {
    # ── 抽象介面:基底類別宣告,由子類別實作 ──────────────
    "cancel_all": "ExchangeAdapter 抽象方法,標準合約明確 NotSupported",
    "funding_rates": "ExchangeAdapter 抽象方法",
    "has_funding": "型別層的能力宣告,由 adapter 各自回答",
    # ── 框架覆寫:由 BaseHTTPRequestHandler 呼叫 ──────────
    "do_GET": "http.server 框架呼叫,不是我們呼叫",
    "log_message": "http.server 框架覆寫(把預設 log 關掉)",
    # ── 動態註冊:sentinel 用名稱前綴掃出來跑 ────────────
    **{f"chk_{k}": "sentinel 以 chk_ 前綴動態註冊執行"
       for k in ("hunter", "round_duration", "service_agmcis",
                 "stale_process", "traceback", "wild_budget",
                 "wild_engine", "wild_learning", "wild_parse",
                 "wild_service")},
    # ── 實盤路徑:LIVE_ENABLED=False,整條鏈都還沒通電 ────
    "plan_backstop": "實盤下單路徑;手動送單期間用不到",
    "coinm_open_orders": "幣本位實盤路徑,目前不做幣本位",
    "load_orders": "實盤訂單持久化;紙上交易不經過它",
    # ── 研究用:由 scripts/ 的分析工具按需呼叫 ────────────
    "indicator_rule": "指標策略族,研究迴路還沒納入(§23~25)",
    "top_half_by_momentum_strength": "動能篩選假說,未納入網格",
    "scaled": "回撤契約縮放;目前由 vol_target 直接封頂",
    "measure_contract_size": "規格反推的量測工具,由探針腳本呼叫",
    "current_funding": "標準合約資金費查詢,成本模型尚未實測接上",
    "measure_impact": "逐幣滑點量測,成本模型尚未接上(見「還沒關掉的洞」)",
    "profile_all": "同上,批次版",
    "slippage_for": "同上,查詢版",
    "is_tradable": "同上,流動性門檻",
    "funding_annual_pct": "成本模型展示用,標準合約成本未實測",
    "holding_cost_pct": "同上",
    "funding_recent_median_8h_pct": "同上",
    "scale_for_contract": "合約乘數縮放;現役七幣乘數都是 1",
    "apply_hysteresis": "動態交易池的遲滯;交易池目前寫死七幣",
    "load_snapshot": "交易池快照讀取;同上",
    # ── 已知但刻意只記錄不擋 ────────────────────────────
    "upcoming": "事件日曆查詢。**只記錄不擋單** —— 見「還沒關掉的洞」",
    "check_freshness": "事件日曆新鮮度;同上",
    # ── 小工具 ────────────────────────────────────────
    "new_trace_id": "logging 內部使用,由 context 設定",
    "is_configured": "Telegram 設定檢查,send() 內部自行判斷",
    "freshness_ms": "行情新鮮度,由 health 另行計算",
    "ohlc_sanity": "K 棒健全性,history 層另有檢查",
    "get_klines": "舊 client 的方法,history 走 load_or_download",
    "get_funding_rate": "同上",
    # ── 這一個不一樣:它是這次反查最重要的發現 ──────────
    "unprotected": (
        "⚠️ docstring 自稱「實盤最重要的一條巡檢」而沒有人呼叫。"
        "手動路徑現在就在跑,而 StandardPosition 根本沒有止損欄位 —— "
        "看不看得到由 scripts/probe_stop_visibility.py 回答。"
        "**這是一個開著的洞,不是一個安心的例外。**"),
}


def _scan() -> dict:
    defined, used = {}, collections.Counter()
    for f in sorted(ROOT.rglob("*.py")):
        if set(f.parts) & SKIP_DIRS:
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not n.name.startswith("_"):
                    defined.setdefault(n.name,
                                       f"{f.relative_to(ROOT)}:{n.lineno}")
            elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                used[n.id] += 1
            elif isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Load):
                used[n.attr] += 1
    return {k: v for k, v in defined.items() if used[k] == 0}


def test_every_uncalled_function_has_been_looked_at():
    """**新出現的「沒人呼叫」必須被解釋。**

    不是要求全部接上 —— 是要求每一個都被看過一次。清單長了就會紅,
    而紅的時候唯一的處置是:接上它、刪掉它,或寫一行為什麼它可以躺著。
    """
    dead = _scan()
    unreviewed = {k: v for k, v in dead.items() if k not in DORMANT}
    assert not unreviewed, (
        "這些公開函式沒有任何地方呼叫,而且沒有人審視過:\n  "
        + "\n  ".join(f"{k}  ({v})" for k, v in sorted(unreviewed.items()))
        + "\n\n接上它、刪掉它,或在 DORMANT 裡寫一行為什麼它可以躺著。"
          "\n**一個看起來存在、實際無作用的東西,比沒有更糟。**")


def test_the_dormant_list_does_not_rot():
    """躺著的清單本身也會過期 —— 已經接上的東西要從清單移走,
    否則它會變成一份沒有人相信的名單。"""
    dead = _scan()
    stale = sorted(set(DORMANT) - set(dead))
    assert not stale, (
        "這些已經有人呼叫了,請從 DORMANT 移走:\n  " + "\n  ".join(stale))


def test_the_stop_check_is_flagged_as_a_hole_not_an_exception():
    """`unprotected()` 躺著不是「沒關係」——

    指令單上最重的一句話是「止損一定要設」,而沒有任何東西驗證它。
    這條測試釘住:那個條目必須明說它是一個洞。
    """
    assert "開著的洞" in DORMANT["unprotected"]

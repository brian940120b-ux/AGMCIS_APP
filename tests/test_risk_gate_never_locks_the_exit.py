"""風控閘不可以把出口一起鎖上 · 2026-09-25

═══ 這條測試守的是**同一個形狀的第二次死結** ═══
資金費那個死結剛修好,PRIMARY 補跑記帳,然後撞上這個:

    **風控否決** 同時持倉檔數:48 檔(上限 25)
    成交 0 筆
    在倉 48 檔

那批訂單裡有 38 張是**平倉單**(目標池只有 10 檔)。而帳戶層檢查
不過時,原本的做法是 `orders_in = []` —— 整批丟掉。

結果:**風控因為「持倉太多」而否決了「減少持倉」的那些單。**
於是 48 檔永遠是 48 檔,明天照樣否決。

一個阻止你降風險的風控閘,是反過來的。修法跟上一個死結同一條原則:
**拒絕仍然在,但不准把出口一起鎖上。**

⚠️ 判準是「成交後 |部位| 會不會變小」,不是看 BUY / SELL ——
   空單的減倉是 BUY,多單的減倉是 SELL,看方向會弄反。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from portfolio.account import Position  # noqa: E402
from portfolio.risk import reduces_exposure  # noqa: E402


class _Acct:
    def __init__(self, **pos):
        self.positions = {k: Position(symbol=k, position_amt=v)
                          for k, v in pos.items()}


class _Ord:
    def __init__(self, symbol, side, qty):
        self.symbol, self.side, self.qty = symbol, side, qty


def test_closing_a_long_counts_as_reducing():
    a = _Acct(X=5.0)
    assert reduces_exposure(_Ord("X", "SELL", 5.0), a) is True


def test_trimming_a_long_counts_as_reducing():
    a = _Acct(X=5.0)
    assert reduces_exposure(_Ord("X", "SELL", 2.0), a) is True


def test_adding_to_a_long_does_not():
    a = _Acct(X=5.0)
    assert reduces_exposure(_Ord("X", "BUY", 2.0), a) is False


def test_closing_a_short_is_a_buy_and_still_counts():
    """**看方向會弄反。** 空單的減倉是 BUY。"""
    a = _Acct(S=-5.0)
    assert reduces_exposure(_Ord("S", "BUY", 5.0), a) is True
    assert reduces_exposure(_Ord("S", "SELL", 2.0), a) is False


def test_a_symbol_we_do_not_hold_is_always_a_new_position():
    a = _Acct(X=5.0)
    assert reduces_exposure(_Ord("NEW", "BUY", 1.0), a) is False
    assert reduces_exposure(_Ord("NEW", "SELL", 1.0), a) is False


def test_flipping_past_zero_is_not_a_reduction():
    """多 5 張反手成空 5 張:|部位| 沒有變小,那是換邊不是降風險。"""
    a = _Acct(X=5.0)
    assert reduces_exposure(_Ord("X", "SELL", 10.0), a) is False


def test_the_tick_keeps_reducing_orders_when_risk_rejects():
    """否決的時候,平倉單必須留下來 —— 否則 48 檔永遠是 48 檔。"""
    src = (ROOT / "portfolio/paper.py").read_text(encoding="utf-8")
    body = src[src.index("    orders_in = list(p[\"orders\"])"):
               src.index("    # ── 二、執行今日訂單")]
    assert "reduces_exposure" in body, "否決時把平倉單也丟掉了"
    assert "擋新倉是風控,擋出場不是" in body
    # 把註解剝掉再查 —— 不然這條會被我自己寫的「原本這裡是
    # `orders_in = []`」那句說明騙過去,變成測散文而不是測程式碼。
    code = "\n".join(ln.split("#", 1)[0] for ln in body.splitlines())
    assert "orders_in = []" not in code, "整批丟掉的寫法還在"


def test_the_rejection_itself_is_still_in_force():
    """**不是把風控放寬。** 開新倉仍然被擋住。"""
    src = (ROOT / "portfolio/paper.py").read_text(encoding="utf-8")
    assert "**風控否決**" in src, "否決的紀錄不見了"


def test_force_is_documented_as_repair_only_with_its_safety_argument():
    """`force` 必須寫清楚**為什麼安全**,不是「應該沒事」。

    2026-09-25:當天的 tick 已經跑過,但那一版風控把平倉單連同開倉單
    一起丟掉,成交 0 筆。於是冪等那道鎖,鎖住的是一個**已知是錯的**
    狀態 —— 今天的額度被一次什麼都沒做的 tick 用掉了。

    跳過那道鎖之所以安全,理由是結構性的,不是運氣:
      · 資金費區間左開右閉,上一次已把 funding_through_ms 推到剛剛,
        再跑一次那段裡沒有新結算 = 0
      · 訂單走「目標權重 − 現有權重」的差額,前次成交 0 筆所以差額沒變

    這條測試守的是那兩句話還在 —— 一個沒有寫明理由的後門,
    下一個人會把它當成日常流程用。
    """
    src = (ROOT / "portfolio/paper.py").read_text(encoding="utf-8")
    # 從 def tick( 切到它自己的結尾。用 "cfg = cfg or MAIN" 當終點會
    # 抓到 plan() 裡先出現的那一個(它在檔案更前面),切出空字串 ——
    # 而空字串會讓下面每一條 assert 都變成在測空氣。
    i = src.index("def tick(")
    body = src[i:i + src[i:].index("\ndef ", 1)]
    assert len(body) > 500, "切出來的 tick() 太短,切法壞了"
    assert "左開右閉" in body, "沒有寫明資金費為什麼不會重複收"
    assert "差額" in body, "沒有寫明訂單為什麼不會重複下"
    assert "不要拿它當每日流程" in body


def test_force_defaults_to_off():
    """後門預設關著。開著的後門不是後門,是大門。"""
    import inspect

    from portfolio.paper import tick
    assert inspect.signature(tick).parameters["force"].default is False


def test_the_rescue_script_only_forces_when_the_account_is_out_of_bounds():
    """**不是無條件重跑。** 只有在倉超過硬上限時才動用。"""
    sh = (ROOT / "scripts/rescue.sh").read_text(encoding="utf-8")
    assert "over = len(a0.positions) > paper.MAX_UNIVERSE" in sh
    assert "if not over:" in sh
    assert "在倉沒有超過池子上限 —— 不需要重跑" in sh
    assert "force=True" in sh

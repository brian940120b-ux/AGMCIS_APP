"""收不到資金費的部位,要被關掉,不是把整本帳凍住 · 2026-09-25

═══ 這條測試守的是一次已經發生、而且持續了 133 小時的停擺 ═══
tick() 原本是一行:

    daily_rates = {s: specs.funding_rate_sum(s, since_ms, now_ms)
                   for s in a.positions}

funding_rate_sum() **刻意**在資料不全時拋 SpecMissing —— 那個拒絕
是對的。問題是沒有人接,所以持倉裡只要有**任何一檔**沒有費率歷史,
tick() 當場死掉,而它後面的每一件事都不會發生 —— **包括賣掉那一檔**。

死結:進不了資金費那一關,就永遠輪不到出場那一關。
實測 PRIMARY:48 檔裡 5 檔沒有費率歷史(池子上限是 10),
卡在 2026-09-19,一動也不動了五天半。

═══ 分辨的準則是「還要不要留」,不是「有沒有資料」═══
  · 在今天的交易池裡 → 第四關漏了它,**照舊拋**
  · 不在交易池裡     → 今天本來就要賣掉,記 0、吼出來、讓它出場
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from portfolio import paper, specs  # noqa: E402


class _Acct:
    def __init__(self, positions):
        self.positions = dict.fromkeys(positions, 1.0)


@pytest.fixture
def rates(monkeypatch):
    """只有 GOOD-USDT 有費率歷史;其他一律 SpecMissing。"""
    def fake_sum(sym, a, b):
        if sym == "GOOD-USDT":
            return 0.001
        raise specs.SpecMissing(f"沒有 {sym} 的資金費率歷史 —— 不猜一個數字")

    monkeypatch.setattr(specs, "funding_rate_sum", fake_sum)
    monkeypatch.setattr(specs, "refresh_funding", lambda s, **k: 0)


def test_a_symbol_being_exited_does_not_kill_the_tick(rates):
    """要賣掉的那一檔不該擋住整本帳。"""
    a = _Acct(["GOOD-USDT", "DEAD-USDT"])
    p = {"symbols": ["GOOD-USDT"]}              # DEAD 不在今天的池子裡
    out, gaps = paper._funding_for(a, p, 0, 86_400_000)
    assert gaps == ["DEAD-USDT"]
    assert out["DEAD-USDT"] == 0.0
    assert out["GOOD-USDT"] == 0.001


def test_a_symbol_we_still_hold_on_purpose_still_raises(rates):
    """還要繼續持有卻沒有資料 = 第四關漏了。**拒絕仍然在。**

    這條在的理由:把「不能記帳」跟「不能出場」解耦,不等於
    可以用不完整的資料替一個要留著的部位記帳。那是兩件事。
    """
    a = _Acct(["BAD-USDT"])
    p = {"symbols": ["BAD-USDT"]}               # 今天還要留著它
    with pytest.raises(specs.SpecMissing):
        paper._funding_for(a, p, 0, 86_400_000)


def test_it_tries_a_backfill_before_giving_up(monkeypatch):
    """放棄之前要先補抓一次 —— 大多數缺漏補一次就好了。"""
    state = {"fetched": False}

    def fake_sum(sym, a, b):
        if state["fetched"]:
            return 0.002
        raise specs.SpecMissing("還沒抓")

    monkeypatch.setattr(specs, "funding_rate_sum", fake_sum)
    monkeypatch.setattr(specs, "refresh_funding",
                        lambda s, **k: state.__setitem__("fetched", True))
    a = _Acct(["X-USDT"])
    out, gaps = paper._funding_for(a, {"symbols": ["X-USDT"]}, 0, 86_400_000)
    assert gaps == [] and out["X-USDT"] == 0.002


def test_the_gaps_are_reported_out_of_tick(rates):
    """少收的東西要看得見 —— 不然它就跟沒少收一樣。"""
    src = (ROOT / "portfolio/paper.py").read_text(encoding="utf-8")
    assert '"funding_gaps": gaps,' in src, "tick() 沒有把缺漏回報出去"


def test_nothing_is_charged_silently(rates):
    """記 0 的時候必須 log.error,不能只是回一個 0。"""
    src = (ROOT / "portfolio/paper.py").read_text(encoding="utf-8")
    body = src[src.index("def _funding_for("):src.index("def plan(")]
    assert "log.error" in body

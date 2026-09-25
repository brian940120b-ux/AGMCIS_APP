"""資金費快取只進不出 · 2026-09-25

═══ 這組測試守的是一個已經在線上發生過的 bug ═══
`refresh_funding()` 首版把結果從空的 dict 開始組,抓完整個蓋回檔案。
於是 **`refresh_funding(["BTC-USDT"])` 會把其他所有幣刪掉。**

而 `paper.screened_universe()` 正是在迴圈裡一個一個呼叫它:

    for sym in picked:
        specs.refresh_funding([sym])       # ← 每一次把前一次洗掉

跑完只剩最後一個幣。接著 `tick()` 走到 `funding_rate_sum()`,
那支**刻意**在資料不全時拋 SpecMissing(不准靜靜少收資金費、
美化績效)—— 記帳當場死掉。

2026-09-25 的直接證據:hunt.py 在 VPS 上回報七個核心幣的費率歷史
**全部**抓不到,而 daily.py 每天都在抓它們。

這跟 2026-09-13 停機 73.7 小時是同一個形狀:一個篩選交易池的步驟,
把記帳弄死了。那次是幣本來就沒有歷史,這次是我們自己刪掉的。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from portfolio import specs  # noqa: E402


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """把快取指到暫存檔,並清掉行程內的記憶體快取。"""
    p = tmp_path / "bingx_funding.json"
    monkeypatch.setattr(specs, "FUNDING_CACHE", p)
    specs._FUND.clear()
    yield p
    specs._FUND.clear()


def _seed(path, data):
    path.write_text(json.dumps({"updated": 0, "symbols": data}),
                    encoding="utf-8")
    specs._FUND.clear()


def _fake_fetch(monkeypatch, rows_by_symbol):
    """攔掉網路,讓 refresh_funding 拿到我們給的資料。"""
    class _Resp:
        def __init__(self, body):
            self._b = body

        def read(self):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(url, timeout=None):
        sym = url.split("symbol=")[1].split("&")[0]
        rows = rows_by_symbol.get(sym, [])
        body = json.dumps({
            "code": "0",
            "data": [{"fundingTime": t, "fundingRate": str(r)}
                     for t, r in rows]}).encode()
        return _Resp(body)

    monkeypatch.setattr(specs.ratelimit, "urlopen", fake_urlopen)


def test_refreshing_one_symbol_does_not_delete_the_others(cache, monkeypatch):
    """**這就是那個 bug。** 補抓 A 不可以讓 B 消失。"""
    _seed(cache, {"BTC-USDT": [{"t": 1000, "rate": 0.0001}],
                  "ETH-USDT": [{"t": 1000, "rate": 0.0002}]})
    _fake_fetch(monkeypatch, {"SOL-USDT": [(2000, 0.0003)]})

    specs.refresh_funding(["SOL-USDT"])

    got = json.loads(cache.read_text(encoding="utf-8"))["symbols"]
    assert set(got) == {"BTC-USDT", "ETH-USDT", "SOL-USDT"}, \
        "補抓一個幣把其他幣刪掉了 —— 記帳會在下一輪 tick 死掉"


def test_the_screened_universe_loop_leaves_every_symbol_intact(
        cache, monkeypatch):
    """重現線上的呼叫方式:逐一補抓。跑完每個幣都要還在。"""
    _seed(cache, {})
    syms = [f"C{i}-USDT" for i in range(6)]
    _fake_fetch(monkeypatch, {s: [(1000 + i, 0.0001)]
                              for i, s in enumerate(syms)})
    for s in syms:                                   # ← paper.py 的寫法
        specs.refresh_funding([s])
    got = json.loads(cache.read_text(encoding="utf-8"))["symbols"]
    assert set(got) == set(syms), f"只剩 {sorted(got)}"


def test_history_for_one_symbol_is_a_union_not_a_replacement(
        cache, monkeypatch):
    """同一個幣也只進不出:交易所只給最近 1000 筆,更早的我們自己留著。"""
    _seed(cache, {"BTC-USDT": [{"t": 100, "rate": 0.1},
                               {"t": 200, "rate": 0.2}]})
    _fake_fetch(monkeypatch, {"BTC-USDT": [(200, 0.2), (300, 0.3)]})
    specs.refresh_funding(["BTC-USDT"])
    rows = json.loads(cache.read_text(encoding="utf-8"))["symbols"]["BTC-USDT"]
    assert [r["t"] for r in rows] == [100, 200, 300], rows


def test_a_first_run_with_no_cache_is_not_an_error(cache, monkeypatch):
    """第一次跑本來就沒有快取 —— 那不是錯誤,不該炸掉。"""
    _fake_fetch(monkeypatch, {"BTC-USDT": [(1, 0.1)]})
    assert specs.refresh_funding(["BTC-USDT"]) == 1


def test_fresh_check_avoids_a_network_round_trip(cache, monkeypatch):
    """已經新的就別再要一次 —— 速率預算是 2 次/秒,候選有 73 個。"""
    import time
    now = int(time.time() * 1000)
    _seed(cache, {"BTC-USDT": [{"t": now - 3600_000, "rate": 0.1}],
                  "OLD-USDT": [{"t": now - 5 * 86_400_000, "rate": 0.1}]})
    assert specs.funding_fresh("BTC-USDT") is True
    assert specs.funding_fresh("OLD-USDT") is False
    assert specs.funding_fresh("NEVER-SEEN") is False


def test_the_live_ledger_still_refuses_incomplete_data(cache):
    """修好快取**不等於**放寬記帳。

    funding_rate_sum 刻意在資料不全時拋例外 —— 靜靜回 0 等於不收
    資金費,帳本、面板、測試全部正常而績效被美化。這條守住那個拒絕。
    """
    _seed(cache, {"BTC-USDT": []})
    day = 24 * 3600 * 1000
    with pytest.raises(specs.SpecMissing):
        specs.funding_rate_sum("BTC-USDT", 0, day)

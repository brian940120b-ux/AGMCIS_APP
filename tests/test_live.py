"""
新系統 — 即時盈虧 · 2026-09-08

═══ 這一層最危險的地方 ═══
即時價格好用,所以很容易不小心讓它流進決策。一旦流進去,
策略就從「日線」變成「盯盤」,而回測的 Calmar 1.33 是在日線規則下
算出來的 —— 那會是第十二次「兩把尺」,而且是最難發現的那種:
每個數字看起來都合理,只有績效悄悄變成另一回事。

所以這裡釘死三件事:
一、即時層只讀不寫(不落地任何檔案、不改帳本)
二、決策路徑不得引用即時層
三、峰值只由記帳更新,即時價不得墊高它
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portfolio import live
from portfolio.account import Account


def test_live_layer_never_writes():
    """即時層不得有任何寫入路徑 —— 結構性保證,不靠自律。

    2026-09-09:原本用純字串比對禁止 "open(",但 urllib 的 urlopen(
    剛好包含這個子字串,加入標記價端點後誤判。守的是「不得寫檔」,
    網路讀取不違反這件事 —— 所以改用詞界比對,把規則寫精確,
    而不是放寬規則。
    """
    import re
    src = Path(live.__file__).read_text(encoding="utf-8")
    for banned in ("write_text", "write_json_atomic", ".save(", "json.dump("):
        assert banned not in src, f"即時層不得出現寫入路徑:{banned}"
    # open( 但不含 urlopen( —— 前面不可以是英數或底線
    assert not re.search(r"(?<![\w.])open\s*\(", src), \
        "即時層不得開檔"
    # 寫入模式的檔案存取一律禁止,連 urlopen 也遮不住
    assert not re.search(r"""["'][wax]b?\+?["']""", src), \
        "即時層不得出現寫入模式的檔案存取"


def test_decision_path_does_not_import_live():
    """訊號 / 規則 / 訂單 / 記帳都不得引用即時層。

    記帳是每日一次、用收盤;即時只給人看。混進去就是第二把尺。
    """
    from portfolio import account, orders, paper, rules
    for mod in (rules, orders, account, paper):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "portfolio.live" not in src and "from portfolio import live" \
            not in src, f"{Path(mod.__file__).name} 不得引用即時層"


def test_drawdown_is_never_negative(monkeypatch):
    """即時權益高於記帳峰值時,回撤要顯示 0,不是負數。

    回撤的定義是「從峰值跌下來多少」,不可能是負的。
    """
    a = Account()
    a.fill("X-USDT", "BUY", 1.0, 100.0, 0.0, "t")
    a.peak_equity = 10_000.0
    monkeypatch.setattr(Account, "load", classmethod(lambda cls: a))
    monkeypatch.setattr(live, "prices", lambda syms=None: {"X-USDT": 500.0})
    s = live.snapshot()
    assert s["equity"] > a.peak_equity
    assert s["drawdown_pct"] == 0.0


def test_missing_quote_is_reported_not_faked(monkeypatch):
    """取不到即時價就要說 —— 絕不拿進場價假裝是即時價而不標記。"""
    a = Account()
    a.fill("X-USDT", "BUY", 1.0, 100.0, 0.0, "t")
    monkeypatch.setattr(Account, "load", classmethod(lambda cls: a))
    monkeypatch.setattr(live, "prices", lambda syms=None: {})
    s = live.snapshot()
    assert s["stale"] == ["X-USDT"]
    assert s["positions"][0]["live"] is False
    assert s["positions"][0]["upnl"] == pytest.approx(0.0)


def test_prices_cache_has_a_short_ttl():
    """快取太長就不是即時,太短會打爆交易所。10 秒。"""
    assert 5.0 <= live.TTL_S <= 30.0


# ══════════════════════════════════════════════════════════
# 串流層(2026-09-08):真正的即時 = 交易所推播,不是輪詢
# ══════════════════════════════════════════════════════════
def test_stream_layer_never_writes():
    """串流層也不得有任何寫入路徑 —— 推播比輪詢更誘人,紀律更要寫死。"""
    from portfolio import stream
    src = Path(stream.__file__).read_text(encoding="utf-8")
    # 檢查要精確:json.dumps 是序列化(訂閱訊息要用),
    # json.dump( 才是寫檔。首版寫成 "json.dump" 把兩者一起擋掉了 ——
    # 一條會誤判的測試,會逼人去放寬測試而不是修程式。
    for banned in ("write_text", "write_json_atomic", ".save(",
                   "json.dump(", "open(", "Path.write"):
        assert banned not in src, f"串流層不得出現寫入路徑:{banned}"


def test_stream_drops_stale_prices():
    """斷線後不得拿最後一次的價格假裝是即時的。

    一條靜止不動卻標著「即時」的價格,比沒有價格危險。
    """
    import time as _t
    from portfolio import stream
    with stream._LOCK:
        stream._PX.clear()
        stream._PX["OLD-USDT"] = (100.0, _t.time() - stream.STALE_S - 5)
        stream._PX["NEW-USDT"] = (200.0, _t.time())
    px = stream.prices()
    assert "OLD-USDT" not in px, "過期價格不得回傳"
    assert px.get("NEW-USDT") == 200.0
    with stream._LOCK:
        stream._PX.clear()


def test_stream_price_wins_over_rest_fallback(monkeypatch):
    """串流有價時必須用串流的 —— REST 只是退路,順序不可顛倒。"""
    from portfolio import stream
    monkeypatch.setattr(stream, "start", lambda *a, **k: None)
    monkeypatch.setattr(stream, "prices",
                        lambda syms=None: {"X-USDT": 111.0})
    live._CACHE.update({"t": 9e18, "px": {"X-USDT": 999.0}, "err": None})
    assert live.prices(["X-USDT"]) == {"X-USDT": 111.0}
    live._CACHE.update({"t": 0.0, "px": {}, "err": None})


def test_stale_seconds_is_short_enough_to_notice_a_disconnect():
    from portfolio import stream
    assert 5.0 <= stream.STALE_S <= 60.0

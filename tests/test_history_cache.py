"""
AGMCIS — 歷史快取測試(2026-09-06)

守的是三件事:
  兩邊都補    首版只往前補新資料,不往回補歷史 —— 實測 4h 卡在 41 天,
              比舊版(每次重抓 333 天)還糟。累積式快取必須能往回長。
  只進不出    交易所不再提供的資料,本地永不刪除。系統看過的就是資產。
  不重抓      本地已是最新時完全不打網路 —— 舊版每天重抓是 6GB 的來源。
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

pytest.importorskip("pydantic")

from market_data import history as H
from models.market import Kline

UTC = timezone.utc


def _k(t, c=100.0):
    return Kline(symbol="X-USDT", interval="1h", open_time=t, open=c, high=c,
                 low=c, close=c, volume=1.0, source="test",
                 fetched_at=datetime.now(UTC))


def _series(start, n, step_h=1):
    return [_k(start + timedelta(hours=step_h * i)) for i in range(n)]


class FakeClient:
    """記錄被要求抓哪些區間,並只供應 available 範圍內的 K 線。"""
    def __init__(self, available):
        self.available = available
        self.calls = []

    def get_klines_history(self, symbol, interval, start, end):
        self.calls.append((start, end))
        return [k for k in self.available if start <= k.open_time <= end]


@pytest.fixture(autouse=True)
def _tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(H, "CACHE_DIR", tmp_path)


def test_first_load_fetches_whole_window_and_persists():
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    c = FakeClient(_series(now - timedelta(hours=200), 201))
    ks = H.load_or_download("X-USDT", 10, client=c, interval="1h")
    assert ks and H._store("X-USDT", "1h").exists()

    # 第二次呼叫會做一次「往回探測」—— 交易所的資料只到 200 小時前,
    # 但要求的是 10 天,系統有理由試一次看看更早的資料在不在。
    H.load_or_download("X-USDT", 10, client=c, interval="1h")
    n_after_probe = len(c.calls)

    # 第三次起必須完全靜默:探測落空的結果已經記住了。
    # 沒有這個記號的話每次呼叫都會重試一次注定失敗的往回抓 ——
    # 研究回合是 1322 配置 × 10 幣,那就是幾千次無效 API 呼叫。
    for _ in range(3):
        H.load_or_download("X-USDT", 10, client=c, interval="1h")
    assert len(c.calls) == n_after_probe


def test_backfills_earlier_history_not_only_newer():
    """核心迴歸:本地只有近期一小段時,要求更長窗口必須往回補。"""
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    full = _series(now - timedelta(hours=500), 501)
    # 先種一份只有最近 50 小時的倉庫
    H._write_atomic(H._store("X-USDT", "1h"), full[-50:])
    c = FakeClient(full)
    ks = H.load_or_download("X-USDT", 20, client=c, interval="1h")
    span = (ks[-1].open_time - ks[0].open_time).total_seconds() / 3600
    assert span > 400, f"只回傳 {span}h,沒有往回補"
    # 確認真的發出了「往回抓」的請求
    assert any(a < full[-50].open_time for a, _ in c.calls)


def test_local_history_survives_when_exchange_no_longer_serves_it():
    """交易所只給近期資料時,本地的深度歷史不得被覆蓋或丟棄。"""
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    deep = _series(now - timedelta(hours=400), 401)
    H._write_atomic(H._store("X-USDT", "1h"), deep)
    # 交易所現在只剩最近 100 小時
    c = FakeClient(deep[-100:])
    ks = H.load_or_download("X-USDT", 30, client=c, interval="1h")
    assert len(ks) == 401
    assert ks[0].open_time == deep[0].open_time


def test_merge_dedupes_and_sorts():
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    a = _series(now - timedelta(hours=10), 5)
    b = _series(now - timedelta(hours=8), 5)      # 與 a 有重疊
    m = H._merge(a, b)
    ts = [k.open_time for k in m]
    assert ts == sorted(ts)
    assert len(ts) == len(set(ts))


def test_download_failure_falls_back_to_local():
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    have = _series(now - timedelta(hours=100), 60)
    H._write_atomic(H._store("X-USDT", "1h"), have)

    class Boom:
        def get_klines_history(self, *a, **k):
            raise RuntimeError("API 掛了")

    ks = H.load_or_download("X-USDT", 30, client=Boom(), interval="1h")
    assert len(ks) == 60          # 拿本地的,不炸掉呼叫端


def test_deeper_request_than_before_does_retry():
    """記號只擋「同樣深度」的重試;要求比以前更深時必須再試一次。"""
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    full = _series(now - timedelta(hours=500), 501)
    H._write_atomic(H._store("X-USDT", "1h"), full[-50:])
    c = FakeClient(full[-100:])          # 交易所只給最近 100 根
    H.load_or_download("X-USDT", 5, client=c, interval="1h")
    n1 = len(c.calls)
    H.load_or_download("X-USDT", 5, client=c, interval="1h")
    assert len(c.calls) == n1            # 同深度 → 不重試
    H.load_or_download("X-USDT", 25, client=c, interval="1h")
    assert len(c.calls) > n1             # 更深 → 再試一次


def test_cache_key_has_no_date_component():
    """舊版把日期放進鍵,導致每天重抓 —— 這是 6GB 與窗口縮水的根因。"""
    p = H._store("X-USDT", "15m")
    assert p.name == "X-USDT_15m.csv"
    assert not H._LEGACY_RE.match(p.stem)

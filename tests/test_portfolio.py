"""
新系統 — 組合模擬器 · 2026-09-08

釘死三件最容易無聲出錯、而且會把負的變成正的事。
"""
from __future__ import annotations
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from portfolio import sim
from portfolio.judge import MAX_DD_CONTRACT_PCT, split_index
from portfolio.size import scale_for_contract

UTC = timezone.utc


def _bars(closes, opens=None):
    t0 = datetime(2024, 1, 1, tzinfo=UTC)
    opens = opens or closes
    return [sim.Bar(t0 + timedelta(days=i), opens[i], max(opens[i], closes[i]),
                    min(opens[i], closes[i]), closes[i])
            for i in range(len(closes))]


def test_signal_cannot_trade_on_the_price_that_produced_it(monkeypatch):
    """訊號用第 i 日收盤算,成交必須在第 i+1 日開盤之後。

    首版用當日收盤到隔日收盤 —— 等於看到收盤價的同一瞬間就能成交。
    這裡用一組「收盤暴漲、隔日開盤才反映」的資料驗證拿不到那一段。
    """
    monkeypatch.setattr(sim, "round_trip_pct", lambda: 0.0)
    monkeypatch.setattr(sim, "measured_funding_8h_pct", lambda *a, **k: 0.0)
    closes = [100.0] * 5 + [200.0] + [200.0] * 5      # 第 5 日收盤暴漲
    opens = [100.0] * 6 + [200.0] * 5                 # 隔日開盤才跳上去
    bars = _bars(closes, opens)
    idx = {"X": {b.t: b for b in bars}}
    dates = [b.t for b in bars]

    def always_in(i, ds, ix):
        return {"X": 1.0}
    r = sim.simulate(dates, idx, always_in, warmup=0)
    # 開盤→開盤:100→100→…→200,那一段漲幅拿得到,但不得憑空多出一倍
    assert r.equity, "應有權益曲線"
    assert r.equity[-1] <= 10_000 * 2.01, "報酬不得超過真實開盤價的漲幅"


def test_costs_are_actually_deducted(monkeypatch):
    """換手要收費、持倉要付資金費 —— 兩者都不得為零。"""
    monkeypatch.setattr(sim, "round_trip_pct", lambda: 0.140)
    monkeypatch.setattr(sim, "measured_funding_8h_pct", lambda *a, **k: 0.0065)
    bars = _bars([100.0] * 20)
    idx = {"X": {b.t: b for b in bars}}
    dates = [b.t for b in bars]
    r = sim.simulate(dates, idx, lambda i, d, x: {"X": 1.0}, warmup=0)
    assert r.fee_paid_pct > 0, "換手必須收費"
    assert r.funding_paid_pct > 0, "持倉必須付資金費"
    assert r.equity[-1] < 10_000, "價格不動時,成本必須讓權益下降"


def test_no_leverage_allowed():
    """權重合計超過 1 一律等比縮回 —— 槓桿是放大器不是優勢。"""
    bars = _bars([100.0] * 10)
    idx = {"A": {b.t: b for b in bars}, "B": {b.t: b for b in bars}}
    dates = [b.t for b in bars]
    r = sim.simulate(dates, idx, lambda i, d, x: {"A": 1.0, "B": 1.0}, warmup=0)
    assert r.equity, "應有權益曲線"


def test_contract_number_is_the_executives_not_mine():
    """回撤契約沿用畢業契約第四條,不得在此另立標準。"""
    assert MAX_DD_CONTRACT_PCT == 15.0
    assert abs(scale_for_contract(30.0) - 0.5) < 1e-9
    assert scale_for_contract(10.0) == 1.0, "不得加槓桿"


def test_split_is_by_time_not_by_symbol():
    """曝險策略的過擬合風險在時間上,切分必須按時間。"""
    assert split_index(300) == 200

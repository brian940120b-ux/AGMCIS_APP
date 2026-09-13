"""
Risk Engine 硬閘 · 2026-09-10(PHASE 1)

═══ 這一組測試守的是什麼 ═══
Master Prompt 第 19 條:Risk Engine 是 HARD GATE,任何 Agent 都不能繞過。
一個「跑得起來但該擋的時候不擋」的風控,比沒有風控危險 ——
它會給人一種已經被保護的錯覺。

所以這裡不測「它會不會放行正常訂單」(那太容易過),
而是**逐條構造會踩線的情境,確認它真的擋下來**。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portfolio import orders as orders_mod
from portfolio.account import Account
from portfolio.risk import ALLOW, REJECT, RiskEngine, RiskLimits


def _acct(equity=10_000.0):
    a = Account()
    a.balance = equity
    a.start_equity = equity
    a.peak_equity = equity
    return a


def _order(sym="ETH-USDT", side="BUY", notional=1000.0, price=2000.0,
           stop=None):
    return orders_mod.Order(symbol=sym, side=side, qty=notional / price,
                            price=price, notional=notional, reason="test",
                            stop=stop)


# ══════════════════════════════════════════════════════════
# 一、帳戶層:任何一條踩線 → 整批拒絕
# ══════════════════════════════════════════════════════════
def test_drawdown_breach_rejects_everything():
    """回撤超過上限就不准開新倉 —— 這是最基本的一條。"""
    a = _acct()
    a.peak_equity = 10_000.0
    a.balance = 7_000.0            # 回撤 30% > 20%
    rd = RiskEngine().evaluate(a, [_order()], {}, [])
    assert rd.verdict == REJECT
    assert not rd.allowed
    assert any("回撤" in c.name and not c.passed for c in rd.checks)


def test_daily_loss_breach_rejects_everything():
    """單日虧損超過上限就停手。"""
    a = _acct()
    a.balance = 9_400.0                       # 較前一日 10,000 虧 6% > 5%
    curve = [{"equity": 10_000.0}]
    rd = RiskEngine().evaluate(a, [_order()], {}, curve)
    assert rd.verdict == REJECT
    assert any("單日虧損" in c.name and not c.passed for c in rd.checks)


def test_liquidation_distance_breach_rejects_everything():
    """強平距離太近就不准再開倉。

    這條直接來自 2026-09-10 的事故:20× 槓桿下六個倉全在懸崖邊,
    BNB 距強平只剩 0.14%,而當時沒有任何規則會擋下那個狀態。
    """
    a = _acct()
    a.fill("BTC-USDT", "BUY", 0.1, 100_000.0, 0.0, "t", leverage=20.0)
    marks = {"BTC-USDT": 100_000.0}           # 20× → 距強平僅 4.5%
    rd = RiskEngine().evaluate(a, [_order()], marks, [])
    assert rd.verdict == REJECT
    assert any("強平距離" in c.name and not c.passed for c in rd.checks)


def test_safe_leverage_passes_the_same_check():
    """同樣的部位改成 3× 就該放行 —— 證明擋的是風險,不是「有部位」。"""
    a = _acct()
    a.fill("BTC-USDT", "BUY", 0.1, 100_000.0, 0.0, "t", leverage=3.0)
    marks = {"BTC-USDT": 100_000.0}           # 3× → 距強平 32.8%
    rd = RiskEngine().evaluate(a, [], marks, [])
    assert rd.verdict == ALLOW
    assert all(c.passed for c in rd.checks)


def test_stale_data_rejects_everything():
    """行情過期就不交易 —— 拿舊價格做決策比不決策危險。"""
    rd = RiskEngine().evaluate(_acct(), [_order()], {}, [],
                               data_age_hours=99.0)
    assert rd.verdict == REJECT
    assert any("新鮮度" in c.name and not c.passed for c in rd.checks)


# ══════════════════════════════════════════════════════════
# 二、逐單層:踩線的那一單被剔除,其餘照走
# ══════════════════════════════════════════════════════════
def test_oversized_symbol_exposure_is_rejected_per_order():
    """單幣曝險過大只拒那一單,不牽連其他單。"""
    a = _acct()
    big = _order("ETH-USDT", notional=5_000.0)     # 50% > 20%
    ok = _order("BTC-USDT", notional=1_000.0)      # 10%
    rd = RiskEngine().evaluate(a, [big, ok], {}, [])
    assert "ETH-USDT" in rd.rejected_symbols
    assert "BTC-USDT" not in rd.rejected_symbols
    assert rd.allowed, "逐單拒絕不該讓整批陣亡"


def test_oversized_single_trade_risk_is_rejected():
    """到出場線的風險超過單筆上限 → 拒單。"""
    a = _acct()
    # 名目 1000、出場線離現價 50% → 風險 500 = 權益的 5% > 2%
    bad = _order("ETH-USDT", notional=1_000.0, price=2_000.0, stop=1_000.0)
    rd = RiskEngine().evaluate(a, [bad], {}, [])
    assert "ETH-USDT" in rd.rejected_symbols


def test_reasonable_trade_risk_passes():
    """出場線很近的單應該放行 —— 證明門檻不是一律拒絕。"""
    a = _acct()
    good = _order("ETH-USDT", notional=1_000.0, price=2_000.0, stop=1_900.0)
    rd = RiskEngine().evaluate(a, [good], {}, [])
    assert "ETH-USDT" not in rd.rejected_symbols
    assert rd.verdict == ALLOW


# ══════════════════════════════════════════════════════════
# 三、結構性保證
# ══════════════════════════════════════════════════════════
def test_engine_failure_rejects_rather_than_allows():
    """風控自己壞掉時必須拒絕,不能放行。

    舊系統教訓五:稽核自己也會壞,而且壞得無聲。
    一個「出錯就放行」的風控,等於沒有風控。
    """
    class Broken:
        positions = {}

        def equity(self, marks):
            raise RuntimeError("模擬帳本損壞")

    rd = RiskEngine().evaluate(Broken(), [_order()], {}, [])
    assert rd.verdict == REJECT
    assert not rd.allowed


def test_limits_are_configurable_not_hardcoded():
    """門檻必須可設定(Master Prompt 第 20 條)。"""
    tight = RiskLimits(max_drawdown_pct=1.0)
    a = _acct()
    a.peak_equity = 10_000.0
    a.balance = 9_800.0                        # 回撤 2% > 1%
    assert RiskEngine(tight).evaluate(a, [], {}, []).verdict == REJECT
    loose = RiskLimits(max_drawdown_pct=50.0)
    assert RiskEngine(loose).evaluate(a, [], {}, []).verdict == ALLOW


def test_every_check_explains_itself():
    """每條檢查都要有可讀的理由 —— 不給理由的拒絕無法判斷對錯。"""
    rd = RiskEngine().evaluate(_acct(), [_order()], {}, [],
                               data_age_hours=1.0)
    assert rd.checks
    for c in rd.checks:
        assert c.name and c.detail, f"{c} 缺少名稱或理由"


# ══════════════════════════════════════════════════════════
# 四、結構性保證:記帳路徑不得繞過風控
# ══════════════════════════════════════════════════════════
def test_tick_routes_orders_through_the_risk_engine():
    """paper.tick() 必須把訂單送過風控才執行。

    Master Prompt 第 19 條:任何 Agent 都不能繞過 Risk Engine。
    這條測試檢查的是**結構**,不是行為 —— 因為「風控有被呼叫」與
    「風控擋得住」是兩件事,後者由前面那些測試負責。
    若日後有人為了方便把 ex.submit(p["orders"]) 改回去,這裡會紅。
    """
    from pathlib import Path

    from portfolio import paper
    src = Path(paper.__file__).read_text(encoding="utf-8")
    body = src[src.index("def tick("):]
    risk_at = body.index("RiskEngine")
    submit_at = body.index("ex.submit(")
    assert risk_at < submit_at, "風控必須在下單**之前**"
    assert "ex.submit(orders_in" in body, \
        "必須送風控過濾後的 orders_in,不得直接送 p['orders']"
    assert 'ex.submit(p["orders"]' not in body, \
        "偵測到繞過風控的下單路徑"


def test_risk_verdict_is_recorded_not_only_logged():
    """風控否決必須落進權益曲線,不能只寫 log。

    憲法第十條:一個只報自己好消息的面板,是在幫操作者自我欺騙。
    2026-09-10 的強平就是只寫 log,結果日報照樣寫「✅ 回撤 1.38%」。
    """
    from pathlib import Path

    from portfolio import paper
    src = Path(paper.__file__).read_text(encoding="utf-8")
    assert '"risk_verdict"' in src, "風控判決必須寫進權益曲線"
    assert '"risk_rejected"' in src

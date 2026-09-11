"""
模擬盤的成本模型。

**刻意與回測共用同一個 CostModel**(agmcis/backtest/costs.py)。

兩邊用不同的成本假設,模擬盤就沒辦法拿來驗證回測 —— 而那正是模擬盤的用途:
回測說這個策略有優勢,模擬盤要在同樣的成本條件下重現它。
費率不同時,你分不出「策略不行」與「成本假設不同」。

Phase 10 之前模擬盤**完全沒有成本**:沒有手續費、沒有滑點、沒有點差、
沒有資金費用、沒有強平。那種損益是理想化的上界,不是可達成的績效。
"""
from agmcis.backtest.costs import CostModel
from agmcis.config import settings


def build_cost_model():
    return CostModel(
        maker_fee=settings.PAPER_MAKER_FEE,
        taker_fee=settings.PAPER_TAKER_FEE,
        slippage_pct=settings.PAPER_SLIPPAGE_PCT,
        spread_pct=settings.PAPER_SPREAD_PCT,
        funding_rate_8h=settings.PAPER_FUNDING_RATE_8H,
        liquidation_fee=settings.PAPER_LIQUIDATION_FEE,
    )


_MODEL = None


def get_cost_model():
    global _MODEL
    if _MODEL is None:
        _MODEL = build_cost_model()
    return _MODEL


def set_cost_model(model):
    """測試用。傳 None 會回到依設定建立的模型。"""
    global _MODEL
    _MODEL = model


def maintenance_margin_ratio():
    return settings.PAPER_MAINTENANCE_MARGIN_RATIO


def fill_price(price, is_long, is_entry, model=None):
    """
    實際成交價。市價單一定吃到點差的一半加上滑點,而且永遠是不利的一邊。

    進場做多 -> 買貴一點;出場做多(賣出)-> 賣便宜一點。兩邊都對自己不利。
    """
    model = model or get_cost_model()
    if is_entry:
        return model.entry_price(price, is_long)
    return model.exit_price(price, is_long)


def liquidation_price(entry_price, leverage, is_long, mmr=None):
    """
    約略的強平價。與回測引擎同一條公式:價格逆向走 (1 - mmr)/leverage。

    真實的 BingX 強平價依合約分層維持保證金率而異,這裡用保守固定值。
    Phase 11 會用真實合約規格校準。
    """
    if not leverage or leverage <= 0:
        return None

    mmr = maintenance_margin_ratio() if mmr is None else mmr
    move = (1 - mmr) / float(leverage)

    return (
        float(entry_price) * (1 - move) if is_long
        else float(entry_price) * (1 + move)
    )

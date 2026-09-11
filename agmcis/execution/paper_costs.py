"""
模擬盤的成本模型。

**刻意與回測共用同一個 CostModel**(agmcis/backtest/costs.py)。

兩邊用不同的成本假設,模擬盤就沒辦法拿來驗證回測 —— 而那正是模擬盤的用途:
回測說這個策略有優勢,模擬盤要在同樣的成本條件下重現它。
費率不同時,你分不出「策略不行」與「成本假設不同」。

Phase 10 之前模擬盤**完全沒有成本**:沒有手續費、沒有滑點、沒有點差、
沒有資金費用、沒有強平。那種損益是理想化的上界,不是可達成的績效。

Phase 11:費率與維持保證金率改為**優先採用合約規格快照**
(agmcis/exchange/specs.py)。沒有快照時退回 settings 的保守預設,
而且每個取值都帶著來源標記 —— 猜測值不會被當成交易所給的值用。
"""
from agmcis.backtest.costs import CostModel
from agmcis.config import settings
from agmcis.exchange import specs as specs_module


def build_cost_model(symbol=None, store=None):
    """
    symbol 給了而且快照裡有它時,費率用交易所的實際值;否則用設定的保守預設。

    滑點與點差**一律用設定值** —— 那兩個是市場衝擊的估計,
    交易所不會告訴你,也沒有「正確答案」。
    """
    maker = settings.PAPER_MAKER_FEE
    taker = settings.PAPER_TAKER_FEE
    funding = settings.PAPER_FUNDING_RATE_8H

    if symbol:
        store = store or specs_module.get_store()
        if store.is_calibrated(symbol):
            maker, _ = store.maker_fee(symbol)
            taker, _ = store.taker_fee(symbol)
            funding, _ = store.funding_rate_8h(symbol)

    return CostModel(
        maker_fee=maker,
        taker_fee=taker,
        slippage_pct=settings.PAPER_SLIPPAGE_PCT,
        spread_pct=settings.PAPER_SPREAD_PCT,
        funding_rate_8h=funding,
        liquidation_fee=settings.PAPER_LIQUIDATION_FEE,
    )


_MODEL = None


def get_cost_model(symbol=None):
    """
    symbol 有校準過的規格時每次重新建一個(費率依合約而異);
    沒有就用共用的預設模型。
    """
    if symbol and specs_module.get_store().is_calibrated(symbol):
        return build_cost_model(symbol)

    global _MODEL
    if _MODEL is None:
        _MODEL = build_cost_model()
    return _MODEL


def set_cost_model(model):
    """測試用。傳 None 會回到依設定建立的模型。"""
    global _MODEL
    _MODEL = model


def maintenance_margin_ratio(symbol=None, store=None):
    """
    維持保證金率。快照裡有實際值就用實際值。

    這個數字直接決定強平價。用猜的值算出來的強平價,
    在真實行情裡不會準 —— 而它準不準決定的是「會不會爆倉」。
    """
    if symbol:
        store = store or specs_module.get_store()
        value, source = store.maintenance_margin_ratio(symbol)
        if source == specs_module.SOURCE_EXCHANGE:
            return value

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


def liquidation_price(entry_price, leverage, is_long, mmr=None, symbol=None):
    """
    約略的強平價。與回測引擎同一條公式:價格逆向走 (1 - mmr)/leverage。

    真實的 BingX 強平價依合約分層維持保證金率而異,這裡用保守固定值。
    Phase 11 會用真實合約規格校準。
    """
    if not leverage or leverage <= 0:
        return None

    mmr = maintenance_margin_ratio(symbol) if mmr is None else mmr
    move = (1 - mmr) / float(leverage)

    return (
        float(entry_price) * (1 - move) if is_long
        else float(entry_price) * (1 + move)
    )

"""
Exchange Adapter 抽象層 · 2026-09-10(PHASE 2)

守三件事:
一、策略不得直接碰交易所(Master Prompt 第 7 條)
二、紙上與實盤共用同一份規格來源 —— 否則就是第十二次「兩把尺」
三、不支援的東西必須**明確拋出**,不得靜靜回假值(舊系統教訓四)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from exchange.base import ExchangeAdapter
from exchange.bingx.perpetual import BingXPerpetual
from exchange.bingx.standard import (BingXStandard, BingXStandardCoinM,
                                     BingXStandardUSDT)
from exchange.paper import PaperExchange
from exchange.types import (Contract, MarketType, NotSupported, OrderRequest,
                            OrderSide, OrderStatus, OrderType, Unverified)


ADAPTERS = [PaperExchange(), BingXPerpetual(),
            BingXStandardUSDT(), BingXStandardCoinM()]


# ══════════════════════════════════════════════════════════
# 一、介面契約
# ══════════════════════════════════════════════════════════
@pytest.mark.parametrize("ad", ADAPTERS, ids=lambda a: a.name)
def test_every_adapter_declares_market_type_and_live_flag(ad):
    """每個介面都必須標明市場類型與是否為實盤。

    Master Prompt 第 6 條:不得把 Standard 與 Perpetual 混成一種市場。
    """
    assert isinstance(ad, ExchangeAdapter)
    assert isinstance(ad.market_type, MarketType)
    assert isinstance(ad.is_live, bool)


def test_no_adapter_is_live_yet():
    """目前不得有任何介面宣稱自己是實盤。

    實盤三道鎖:LIVE_ENABLED 原始碼常數、契約八條(2/8)、人工簽署。
    若有人把某個 adapter 的 is_live 改成 True,這條會紅。
    """
    for ad in ADAPTERS:
        assert ad.is_live is False, f"{ad.name} 宣稱是實盤"


# ══════════════════════════════════════════════════════════
# 二、規格來源必須共用
# ══════════════════════════════════════════════════════════
def test_paper_and_perpetual_share_the_same_contract_specs():
    """紙上與永續必須拿到完全相同的規格。

    若紙上用假規格,測出來的「可交易性」就是假的 ——
    2026-09-09 的教訓:七個持倉的數量全部不符精度,而紙上完全看不出來。
    """
    p, b = PaperExchange().contracts(), BingXPerpetual().contracts()
    assert p.keys() == b.keys()
    for sym in ("BTC-USDT", "ETH-USDT"):
        assert p[sym] == b[sym], f"{sym} 規格不一致 —— 兩把尺"


def test_contract_knows_whether_it_has_funding():
    """收不收資金費是一個**被查證過的事實**,不是從市場類型推導的。"""
    c = PaperExchange().contracts()["BTC-USDT"]
    assert c.market_type is MarketType.PERPETUAL
    assert c.has_funding is True


def test_unverified_funding_raises_instead_of_answering_false():
    """沒查證過就拋 —— **不准回 False。**

    2026-09-13 的教訓:原本 has_funding 從 market_type 推導,
    對「標準合約」回 False。而 BingX 幣本位標準合約其實是
    Coin-M perpetual,**有**資金費。一個推導出來的錯答案,
    會讓回測少扣一筆持續性成本,而且不會有人發現。
    """
    unknown = Contract(symbol="X", market_type=MarketType.STANDARD,
                       quantity_precision=2, price_precision=2, min_qty=1,
                       min_notional=2, taker_fee_pct=0.05,
                       maker_fee_pct=0.02)
    assert unknown.funding is None
    with pytest.raises(Unverified):
        unknown.has_funding


def test_market_type_no_longer_decides_funding_or_expiry():
    """標準合約**可以**是永續、可以收資金費。型別層必須容得下這件事。"""
    coinm = Contract(symbol="BTC-USD", market_type=MarketType.STANDARD,
                     quantity_precision=0, price_precision=1, min_qty=1,
                     min_notional=10, taker_fee_pct=0.05, maker_fee_pct=0.02,
                     inverse=True, funding=True, expiry=None)
    assert coinm.has_funding is True
    assert coinm.expiry is None
    assert coinm.inverse is True


# ══════════════════════════════════════════════════════════
# 三、不支援的必須明確拋出
# ══════════════════════════════════════════════════════════
def test_usdt_standard_futures_raises_with_a_verifiable_reason():
    """U 本位標準合約必須拋出,而且理由要說得出是交易所端的限制。

    一個「看起來存在、實際無作用」的東西比沒有更糟(舊系統教訓四)。
    """
    ad = BingXStandardUSDT()
    with pytest.raises(NotSupported) as e:
        ad.contracts()
    msg = str(e.value)
    assert "internal testing" in msg
    assert "2026-09-13" in msg, "必須註明查證日期,否則無法判斷是否過時"
    assert "cswap" in msg, "必須指出另一個產品可以下單,否則會再判死一次"


def test_the_old_name_still_points_at_the_usdt_product():
    """BingXStandard 這個舊名指的是 U 本位 —— 別名不得換了意思。"""
    assert BingXStandard is BingXStandardUSDT


def test_coinm_blocks_orders_for_our_own_reason_not_the_exchanges():
    """幣本位擋單的理由必須是**我方帳本還沒改寫**,不是交易所不給。

    這兩個理由的差別決定了下一步是「等交易所」還是「我們去寫」。
    2026-09-10 把後者誤寫成前者,整個市場因此停了三天。
    """
    ad = BingXStandardCoinM()
    req = OrderRequest(symbol="BTC-USD", market_type=MarketType.STANDARD,
                       side=OrderSide.BUY, order_type=OrderType.MARKET,
                       qty=1)
    with pytest.raises(NotSupported) as e:
        ad.submit(req)
    msg = str(e.value)
    assert "反向" in msg, "必須點名反向合約帳本這件事"
    assert "trade/order" in msg, "必須說清楚交易所這邊是通的"


def test_coinm_does_not_pretend_funding_is_zero():
    """幣本位查不到資金費歷史 —— 但**不准因此當成沒有資金費**。"""
    ad = BingXStandardCoinM()
    with pytest.raises(NotSupported) as e:
        ad.funding_rates("BTC-USD", 0, 1)
    assert "當成 0" in str(e.value)


def test_live_trading_paths_raise_not_silently_fail():
    """實盤下單/持倉/餘額都必須明確拒絕,不得回 None 或空值。"""
    ad = BingXPerpetual()
    req = OrderRequest(symbol="BTC-USDT", market_type=MarketType.PERPETUAL,
                       side=OrderSide.BUY, order_type=OrderType.MARKET,
                       qty=0.001)
    for call in (lambda: ad.submit(req), ad.positions, ad.balance,
                 ad.cancel_all):
        with pytest.raises(NotSupported):
            call()


def test_unknown_symbol_raises_instead_of_guessing():
    """查不到規格必須中斷,不得猜一個精度。"""
    with pytest.raises(NotSupported):
        PaperExchange().spec("NOT-A-REAL-SYMBOL")


# ══════════════════════════════════════════════════════════
# 四、紙上成交模型
# ══════════════════════════════════════════════════════════
def test_market_order_pays_slippage_in_the_adverse_direction():
    """市價單的滑點方向永遠對自己不利。"""
    from portfolio.costs import SLIP_FLOOR_PCT
    ad = PaperExchange()
    buy = ad.submit(OrderRequest(
        symbol="ETH-USDT", market_type=MarketType.PERPETUAL,
        side=OrderSide.BUY, order_type=OrderType.MARKET,
        qty=1.0, price=2000.0))
    assert buy.status is OrderStatus.FILLED
    assert buy.avg_price > 2000.0
    assert buy.avg_price == pytest.approx(
        2000.0 * (1 + SLIP_FLOOR_PCT / 100), rel=1e-6)
    sell = ad.submit(OrderRequest(
        symbol="ETH-USDT", market_type=MarketType.PERPETUAL,
        side=OrderSide.SELL, order_type=OrderType.MARKET,
        qty=1.0, price=2000.0))
    assert sell.avg_price < 2000.0


def test_post_only_pays_maker_fee_and_no_slippage():
    """只掛單不吃價差、收 maker 費率 —— 這是掛單省成本的機制。"""
    ad = PaperExchange()
    r = ad.submit(OrderRequest(
        symbol="ETH-USDT", market_type=MarketType.PERPETUAL,
        side=OrderSide.BUY, order_type=OrderType.POST_ONLY,
        qty=1.0, price=2000.0))
    assert r.avg_price == pytest.approx(2000.0), "掛單不該吃滑點"
    spec = ad.spec("ETH-USDT")
    assert r.fee == pytest.approx(
        r.filled_qty * r.avg_price * spec.maker_fee_pct / 100)
    assert spec.maker_fee_pct < spec.taker_fee_pct


def test_quantity_is_truncated_never_rounded_up():
    """數量一律無條件捨去 —— 進位會讓部位大於預期、佔用更多保證金。"""
    ad = PaperExchange()
    assert ad.round_qty("UNI-USDT", 103.9) == 103.0
    assert ad.round_qty("BTC-USDT", 0.00899999) == pytest.approx(0.0089)


def test_too_small_order_is_rejected_not_silently_shrunk():
    """過小的單要明確拒絕,不得靜靜放行或改大。"""
    r = PaperExchange().submit(OrderRequest(
        symbol="BTC-USDT", market_type=MarketType.PERPETUAL,
        side=OrderSide.BUY, order_type=OrderType.MARKET,
        qty=0.00000001, price=78000.0))
    assert r.status is OrderStatus.REJECTED
    assert r.error


# ══════════════════════════════════════════════════════════
# 五、結構性保證
# ══════════════════════════════════════════════════════════
def test_paper_adapter_has_no_exchange_client():
    """紙上介面不得有任何下單路徑 —— 結構性保證,不靠自律。"""
    src = Path(__file__).resolve().parents[1] / "exchange" / "paper.py"
    body = src.read_text(encoding="utf-8")
    for banned in ("requests.post", "urlopen", "api_key", "secret",
                   "hmac", "place_order"):
        assert banned not in body, f"紙上介面不得出現 {banned}"


def test_strategy_layer_does_not_import_exchange_adapters():
    """策略層不得直接碰交易所(Master Prompt 第 7 條)。

        Signal → Risk → Portfolio → Execution → Adapter → BingX

    rules.py 與 signals.py 是純訊號邏輯,它們只該看到價格序列。

    2026-09-10:首版用字串比對「body 裡不得出現 bingx」,結果被**註解**
    誤判(rules.py 的註解解釋了參數為何取自 BingX App 預設值)。
    今天已經犯過一次同樣的錯(urlopen 被 open( 誤判)。
    守的不變量是「不得 import 交易所層」,不是「不得提到交易所」——
    註解說明來源反而是好事。所以改成檢查 import 陳述。
    """
    import ast

    root = Path(__file__).resolve().parents[1]
    for mod in ("portfolio/rules.py", "portfolio/signals.py"):
        tree = ast.parse((root / mod).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        bad = [m for m in imported
               if m.split(".")[0] in ("exchange",) or "bingx" in m.lower()]
        assert not bad, f"{mod} 引用了交易所層:{bad}"

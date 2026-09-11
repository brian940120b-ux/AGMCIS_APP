"""
SAFE LIVE MODE(Master Prompt 第四十六節)。

第一次跑實單的時候,模擬盤驗證過的參數不該直接搬過來。理由不是
參數算錯了,而是模擬盤根本驗不到某些東西 —— 部分成交、掛單被拒、
停損掛不上、延遲、真實滑點。這些只有實單會遇到,而且會在
第一天就遇到。

所以實單模式多一層上限:

    MAX_LIVE_POSITION_SIZE_USDT   單筆名目價值上限
    MAX_LIVE_DAILY_LOSS_USDT      實單當日虧損上限
    MAX_LIVE_TRADES_PER_DAY       實單當日筆數上限
    MAX_LIVE_LEVERAGE             實單槓桿上限

## 這一層只能收緊,不能放寬

這是整個模組唯一重要的規則。實單上限與一般上限取**較嚴格的那一個**:

    MAX_LEVERAGE=5、MAX_LIVE_LEVERAGE=2   ->  實單用 2
    MAX_LEVERAGE=2、MAX_LIVE_LEVERAGE=5   ->  實單用 2

第二種情況看起來像設定錯誤,但這裡不報錯也不採用 5 —— 直接用 2。
把「實單限制」寫成一個可以放寬一般限制的旋鈕,等於留了一個
「切到實單反而變寬鬆」的後門,而那正好是最不該發生的方向。

## 它不是閘門

SAFE LIVE 不決定能不能下實單 —— 那是 Phase 17 的 LIVE SAFETY GATE。
這一層只在已經獲准的前提下,把額度縮小。兩者都要過。
"""
import logging

from agmcis.config import settings
from agmcis.core.enums import TradingMode

logger = logging.getLogger("agmcis.safety.safe_live")


# 一般上限 -> 實單上限。全部的合併規則都是「取絕對值較小的」——
# 這幾個參數的共同性質是「數字越小越保守」。
OVERRIDES = {
    "MAX_LEVERAGE": "MAX_LIVE_LEVERAGE",
    "MAX_DAILY_LOSS_USDT": "MAX_LIVE_DAILY_LOSS_USDT",
    "MAX_TRADES_PER_DAY": "MAX_LIVE_TRADES_PER_DAY",
}

# 這一個沒有對應的一般設定 —— 一般模式不限制單筆名目,
# 名目由「風險 ÷ 停損距離」自然決定。實單才額外壓一個硬上限。
LIVE_NOTIONAL_KEY = "MAX_LIVE_POSITION_SIZE_USDT"


def is_active(mode=None):
    """
    實單模式而且 SAFE_LIVE_MODE 沒有被明確關掉。

    預設開啟,而且關掉需要人工改設定 —— 第一次實單的預設值
    應該是最保守的那一個,不是最方便的那一個。
    """
    if not getattr(settings, "SAFE_LIVE_MODE", True):
        return False

    value = mode if mode is not None else getattr(settings, "TRADING_MODE", "paper")
    parsed = TradingMode.parse(value, TradingMode.PAPER)
    return parsed is TradingMode.LIVE


def tighten(name, base, mode=None):
    """
    套用實單上限。回傳較嚴格的那一個值。

    非實單模式、沒有對應的實單設定、或任一邊不是數字時,原值回傳 ——
    這一層不負責驗證設定,只負責不要放寬。
    """
    if name not in OVERRIDES or not is_active(mode):
        return base

    live_value = getattr(settings, OVERRIDES[name], None)
    if live_value is None:
        return base

    try:
        base_number = float(base)
        live_number = float(live_value)
    except (TypeError, ValueError):
        return base

    # 「較嚴格」= 絕對值較小。虧損上限是負的方向、槓桿與筆數是正的,
    # 但兩者都是「數字越小越保守」,所以比絕對值就對了。
    if abs(live_number) < abs(base_number):
        return live_number if base_number >= 0 else -abs(live_number)

    return base


def max_notional(mode=None):
    """
    實單的單筆名目上限。非實單模式回 None(不限制)。
    """
    if not is_active(mode):
        return None

    value = getattr(settings, LIVE_NOTIONAL_KEY, None)
    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def snapshot(mode=None):
    """目前實際生效的實單上限。給 Dashboard 與 LIVE GATE 用。"""
    active = is_active(mode)

    return {
        "active": active,
        "trading_mode": mode if mode is not None else getattr(
            settings, "TRADING_MODE", "paper",
        ),
        "max_notional_usdt": max_notional(mode),
        "effective": {
            name: tighten(name, getattr(settings, name, None), mode)
            for name in OVERRIDES
        },
        "configured": {
            live_name: getattr(settings, live_name, None)
            for live_name in list(OVERRIDES.values()) + [LIVE_NOTIONAL_KEY]
        },
    }


def missing_settings():
    """
    哪些實單上限沒有設定。LIVE SAFETY GATE 用這個擋住
    「開了實單卻沒有任何實單專屬上限」的情況。
    """
    names = list(OVERRIDES.values()) + [LIVE_NOTIONAL_KEY]
    return [name for name in names if getattr(settings, name, None) is None]

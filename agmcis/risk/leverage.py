"""
槓桿決策。

原本的邏輯是「信心分數越高,槓桿越高」(confidence >= 95 -> 8x)。
這是危險的:信心是對「方向」的判斷,槓桿是對「能承受多少逆向波動」的設定,
兩者沒有因果關係。訊號很強但市場波動很大的時候,高槓桿只會讓你在
正確的方向上被洗出場。

正確的驅動因素:

  1. **強平距離必須比停損遠。** 這是硬性上限,不可協商。
     槓桿 N 倍時,價格逆向走約 1/N 就會接近強平。
     如果停損距離比 1/N 還遠,那麼在停損觸發之前就會先被強平 ——
     等於停損形同虛設,而且虧掉的是全部保證金而不是預設的風險金額。

  2. **波動度。** ATR 佔價格的比例越高,槓桿越低。

  3. **多時間框架一致性。** 框架不同向時降槓桿。

  4. **風控的全域上限。** 最後一道,誰都不能超過。

信心分數**不會**提高槓桿。它最多只能在其他條件都好的時候不降槓桿。
"""
import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger("agmcis.leverage")

# 強平安全係數。價格逆向走 1/leverage 左右就接近強平,
# 但還要扣掉維持保證金與手續費,所以只用理論值的一半當安全邊界。
LIQUIDATION_SAFETY_FACTOR = 0.5

# ATR 佔價格比例對應的槓桿上限
VOLATILITY_CAPS = [
    (0.05, 1.0),   # ATR >= 5%  -> 最多 1x
    (0.03, 2.0),   # ATR >= 3%  -> 最多 2x
    (0.015, 3.0),  # ATR >= 1.5% -> 最多 3x
]


@dataclass
class LeverageDecision:
    leverage: float
    max_allowed: float
    reasons: List[str] = field(default_factory=list)
    caps: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "leverage": self.leverage,
            "max_allowed": self.max_allowed,
            "reasons": list(self.reasons),
            "caps": dict(self.caps),
        }


def max_leverage_for_stop(stop_distance_pct, safety_factor=LIQUIDATION_SAFETY_FACTOR):
    """
    停損距離決定的槓桿上限。

    這是最重要的一條。槓桿 N 倍時,價格逆向走約 1/N 就接近強平。
    要讓停損先於強平觸發,必須:

        停損距離% / 100  <  安全係數 / 槓桿
        => 槓桿 < 安全係數 × 100 / 停損距離%

    停損距離 3% 且安全係數 0.5 時,上限是 16.6x。
    停損距離 10% 時,上限只有 5x —— 停損放得越遠,能用的槓桿越低。
    """
    if not stop_distance_pct or stop_distance_pct <= 0:
        return None
    return safety_factor * 100.0 / float(stop_distance_pct)


def max_leverage_for_volatility(atr, price):
    """ATR 佔價格的比例決定的槓桿上限。取不到資料時回 None(不設限,交給其他條件)。"""
    if not atr or not price or price <= 0:
        return None

    atr_ratio = float(atr) / float(price)

    for threshold, cap in VOLATILITY_CAPS:
        if atr_ratio >= threshold:
            return cap

    return None


def decide(
    stop_distance_pct,
    max_leverage,
    atr=None,
    price=None,
    mtf_score=None,
    mtf_required=3,
    contract_max_leverage=None,
    base_leverage=None,
):
    """
    決定這一筆該用幾倍槓桿。

    回傳 LeverageDecision。leverage 永遠 >= 1。

    ⚠️ 這裡刻意**沒有 confidence 參數**。信心分數不會提高槓桿。
    """
    caps = {}
    reasons = []

    # 起點:風控設定的全域上限,而不是「盡可能高」
    candidate = float(base_leverage) if base_leverage else float(max_leverage)
    caps["risk_limit"] = float(max_leverage)

    # ---- 1. 強平距離(硬性上限)----
    stop_cap = max_leverage_for_stop(stop_distance_pct)
    if stop_cap is None:
        return LeverageDecision(
            leverage=1.0, max_allowed=1.0,
            reasons=["沒有停損距離,槓桿一律降到 1x"],
            caps=caps,
        )

    caps["liquidation_distance"] = round(stop_cap, 4)
    if candidate > stop_cap:
        reasons.append(
            f"停損距離 {stop_distance_pct:.2f}% 需要強平價更遠,"
            f"槓桿由 {candidate:g}x 降到 {stop_cap:.2f}x"
        )
        candidate = stop_cap

    # ---- 2. 波動度 ----
    vol_cap = max_leverage_for_volatility(atr, price)
    if vol_cap is not None:
        caps["volatility"] = vol_cap
        if candidate > vol_cap:
            reasons.append(
                f"ATR 佔價格 {float(atr) / float(price) * 100:.2f}%,"
                f"槓桿由 {candidate:.2f}x 降到 {vol_cap:g}x"
            )
            candidate = vol_cap

    # ---- 3. 多時間框架一致性 ----
    if mtf_score is not None and abs(mtf_score) < mtf_required:
        mtf_cap = 2.0
        caps["mtf"] = mtf_cap
        if candidate > mtf_cap:
            reasons.append(
                f"多時間框架未完全同向(score {mtf_score}),槓桿降到 {mtf_cap:g}x"
            )
            candidate = mtf_cap

    # ---- 4. 合約本身的上限 ----
    if contract_max_leverage:
        caps["contract"] = float(contract_max_leverage)
        if candidate > contract_max_leverage:
            reasons.append(f"合約槓桿上限 {contract_max_leverage}x")
            candidate = float(contract_max_leverage)

    # ---- 5. 全域風控上限(最後一道)----
    if candidate > max_leverage:
        reasons.append(f"風控槓桿上限 {max_leverage}x")
        candidate = float(max_leverage)

    # 一律無條件捨去,不用 round() ——
    # round(16.666, 2) 會得到 16.67,那比真正的上限還高,
    # 等於讓進位偷偷放寬了安全限制。與數量進位的原則一致:
    # 調整只能讓事情更保守,不能更寬鬆。
    leverage = max(1.0, _floor2(candidate))
    max_allowed = max(1.0, _floor2(min(caps.values()))) if caps else leverage

    return LeverageDecision(
        leverage=leverage,
        max_allowed=max_allowed,
        reasons=reasons,
        caps=caps,
    )


def _floor2(value):
    """無條件捨去到小數點後兩位。"""
    return math.floor(float(value) * 100) / 100


def liquidation_distance_pct(leverage):
    """槓桿對應的大約強平距離(%)。用來在 UI 上顯示「你離強平多遠」。"""
    if not leverage or leverage <= 0:
        return None
    return 100.0 / float(leverage)


def stop_is_safer_than_liquidation(stop_distance_pct, leverage,
                                   safety_factor=LIQUIDATION_SAFETY_FACTOR):
    """
    停損會不會在強平之前觸發。False 代表這個組合不安全。

    用 <= 而不是 <,是為了與 decide() 的邊界一致 ——
    decide() 允許槓桿剛好等於 max_leverage_for_stop() 算出的上限。
    兩個函式在邊界上必須同意,否則會出現「decide 批准的槓桿,
    安全檢查卻說不安全」這種自相矛盾。安全邊界由 safety_factor 提供,
    不是靠這裡多扣一點。

    有測試鎖住這個不變量:decide() 產生的槓桿一定通過這個檢查。
    """
    if not stop_distance_pct or not leverage:
        return False
    # 浮點數除法會有尾差,加一個極小的容忍值避免邊界誤判
    return float(stop_distance_pct) <= safety_factor * 100.0 / float(leverage) + 1e-9

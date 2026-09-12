"""
多時間框架階層(Master Prompt 第二十四節)。

第二十四節寫得很具體:

    1D  -> Macro Trend
    4H  -> Major Trend
    1H  -> Structure
    15M -> Setup
    5M  -> Entry

原本的 mtf_engine.calculate_mtf_score() 只是把幾個時間框架的分數
加權平均。那不是階層,是投票 —— 而兩者的差別在一個很重要的地方:

    **投票允許低時間框架蓋過高時間框架。**

15 分鐘線看多、日線看空,加權之後可能得到「偏多」。但實際上那是
在下降趨勢裡的一個反彈,而那正是最容易虧錢的一種 setup。

階層的規則不一樣:**高時間框架決定方向,低時間框架只決定時機。**
日線看空的時候,15 分鐘線再漂亮也不做多 —— 它只能決定「什麼時候做空」。

## 對齊(alignment)不是全部同向

要求五個時間框架全部同向,實務上幾乎不會發生,那個條件等於不交易。
這裡的定義是:

  * **方向**由 1D 與 4H 決定(兩者矛盾 -> 不交易)。
  * **結構**由 1H 確認(與方向矛盾 -> 降級為觀望)。
  * **時機**由 15M / 5M 提供,它們只能加分或不加分,**不能改方向**。

## 資料不足的時間框架

回 UNKNOWN,不回中性。「日線資料拿不到」與「日線是中性的」是完全
不同的兩件事,而把前者當成後者會讓系統在最沒有資訊的時候最敢交易。
"""
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from agmcis.core.enums import Direction

logger = logging.getLogger("agmcis.analysis.mtf")

# 階層由高到低。順序就是權威順序。
HIERARCHY = ("1d", "4h", "1h", "15m", "5m")

ROLES = {
    "1d": "總體趨勢",
    "4h": "主要趨勢",
    "1h": "市場結構",
    "15m": "setup",
    "5m": "進場時機",
}

# 決定方向的時間框架。這兩個矛盾就不交易。
DIRECTIONAL = ("1d", "4h")

# 確認結構的時間框架。
STRUCTURAL = "1h"

# 只能影響時機、不能影響方向的時間框架。
TIMING = ("15m", "5m")


@dataclass
class TimeframeView:
    timeframe: str
    direction: Direction = Direction.WAIT
    known: bool = False
    detail: str = ""

    @property
    def role(self):
        return ROLES.get(self.timeframe, "未定義")

    def to_dict(self):
        return {
            "timeframe": self.timeframe,
            "role": self.role,
            "direction": self.direction.value if self.known else "UNKNOWN",
            "known": self.known,
            "detail": self.detail,
        }


@dataclass
class MTFView:
    direction: Direction = Direction.WAIT
    aligned: bool = False
    timing_score: float = 0.0
    views: List[TimeframeView] = field(default_factory=list)
    blocked_reason: Optional[str] = None
    reasons: List[str] = field(default_factory=list)

    @property
    def is_actionable(self):
        return self.direction.is_directional and self.blocked_reason is None

    def to_dict(self):
        return {
            "direction": self.direction.value,
            "aligned": self.aligned,
            "timing_score": round(self.timing_score, 2),
            "actionable": self.is_actionable,
            "blocked_reason": self.blocked_reason,
            "reasons": list(self.reasons),
            "timeframes": [v.to_dict() for v in self.views],
        }


def _view(timeframe, payload):
    """
    payload 可以是 Indicators、MarketRegime、Direction,或字串。
    拿不到明確方向一律 known=False。
    """
    view = TimeframeView(timeframe=timeframe)

    if payload is None:
        view.detail = "沒有資料"
        return view

    # Indicators:用 trend 屬性
    trend = getattr(payload, "trend", None)
    if isinstance(trend, str):
        if trend == "BULLISH":
            view.direction, view.known = Direction.LONG, True
        elif trend == "BEARISH":
            view.direction, view.known = Direction.SHORT, True
        else:
            view.detail = f"趨勢 {trend}"
        if view.known:
            view.detail = f"趨勢 {trend}"
        return view

    direction = Direction.parse(payload)
    if direction is not None and direction.is_directional:
        view.direction, view.known = direction, True
        view.detail = direction.value
    else:
        view.detail = "方向不明確"

    return view


def analyse(by_timeframe):
    """
    by_timeframe: {"1d": Indicators | Direction | str, ...}

    回傳 MTFView。缺少的時間框架不會讓它崩潰,但**會影響判定** ——
    方向層缺一個就不交易。
    """
    result = MTFView()
    views = {tf: _view(tf, (by_timeframe or {}).get(tf)) for tf in HIERARCHY}
    result.views = [views[tf] for tf in HIERARCHY]

    # ---- 1. 方向:1D 與 4H ----
    directional = [views[tf] for tf in DIRECTIONAL]
    unknown = [v.timeframe for v in directional if not v.known]

    if unknown:
        result.blocked_reason = "高時間框架資料不足"
        result.reasons.append(
            f"{', '.join(unknown)} 沒有可用方向。"
            f"資料拿不到不等於中性 —— 在最沒有資訊的時候最不該交易。"
        )
        return result

    directions = {v.direction for v in directional}
    if len(directions) > 1:
        result.blocked_reason = "高時間框架互相矛盾"
        result.reasons.append(
            " / ".join(f"{v.timeframe} {v.direction.value}" for v in directional)
        )
        return result

    result.direction = directional[0].direction
    result.reasons.append(
        f"1D 與 4H 同為{result.direction.value}"
    )

    # ---- 2. 結構:1H ----
    structure = views[STRUCTURAL]
    if structure.known and structure.direction is not result.direction:
        result.blocked_reason = "1H 結構與高時間框架矛盾"
        result.reasons.append(
            f"1H 是{structure.direction.value},與高時間框架的"
            f"{result.direction.value}相反 —— 這是在趨勢裡的反向段落"
        )
        result.direction = Direction.WAIT
        return result

    if not structure.known:
        result.reasons.append("1H 結構不明確,只靠高時間框架")

    # ---- 3. 時機:15M / 5M。只加分,不改方向 ----
    agreeing = 0
    for timeframe in TIMING:
        view = views[timeframe]
        if not view.known:
            continue
        if view.direction is result.direction:
            agreeing += 1
            result.reasons.append(f"{timeframe} 同向")
        else:
            result.reasons.append(
                f"{timeframe} 反向 —— 只影響進場時機,不改變方向"
            )

    result.timing_score = agreeing / len(TIMING) * 100.0
    result.aligned = (
        structure.known
        and structure.direction is result.direction
        and agreeing == len(TIMING)
    )

    return result


def score(by_timeframe):
    """
    給 scorer 用的 0–100 分數。

    不可交易時回 0,而不是「中性的 50」—— 一個被高時間框架矛盾
    擋下來的訊號,它的多時間框架分數就是 0。
    """
    view = analyse(by_timeframe)
    if not view.is_actionable:
        return 0.0

    # 方向成立就有底分,時機是加分項。
    return round(60.0 + view.timing_score * 0.4, 2)

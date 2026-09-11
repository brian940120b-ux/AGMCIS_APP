"""
Position Sizing。

原本的系統是「倉位固定 1000 USDT,槓桿由信心分數決定」。
這是反過來的 —— 那代表每一筆交易承擔的風險完全取決於停損放多遠,
停損放很遠的那筆會虧掉好幾倍。

正確的順序是:

    1. 先決定「這一筆最多可以虧多少錢」   = 權益 × 風險%
    2. 再由停損距離反推「名目價值要多大」 = 風險金額 ÷ 停損距離%
    3. 最後由槓桿決定「要押多少保證金」   = 名目 ÷ 槓桿

關鍵觀念:**名目價值與槓桿無關。**
名目由風險與停損距離決定,槓桿只影響「這個名目需要壓多少保證金」。
把槓桿調高不會讓你冒更多風險,只會讓同樣的部位佔用比較少的保證金 ——
但它會讓強平價變近,所以槓桿有另一條獨立的限制(見 leverage.py)。
"""
import logging
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger("agmcis.position_sizing")


@dataclass
class SizingResult:
    approved: bool
    size_usdt: Optional[float] = None          # 保證金
    notional: Optional[float] = None           # 名目價值
    risk_usdt: Optional[float] = None          # 觸及停損時會虧掉的金額
    risk_pct_of_equity: Optional[float] = None
    reasons: List[str] = field(default_factory=list)
    adjustments: List[str] = field(default_factory=list)

    @property
    def reason(self):
        return "; ".join(self.reasons) if self.reasons else None

    def to_dict(self):
        return {
            "approved": self.approved,
            "size_usdt": self.size_usdt,
            "notional": self.notional,
            "risk_usdt": self.risk_usdt,
            "risk_pct_of_equity": self.risk_pct_of_equity,
            "reasons": list(self.reasons),
            "adjustments": list(self.adjustments),
        }


def calculate_size(
    equity,
    stop_distance_pct,
    leverage,
    risk_per_trade_pct,
    available_balance=None,
    current_exposure_usdt=0.0,
    max_exposure_pct=None,
    min_notional=None,
    max_notional=None,
):
    """
    計算這一筆該用多少保證金。

    參數:
        equity              帳戶權益
        stop_distance_pct   停損距離佔進場價的百分比(例如 3.0 代表 3%)
        leverage            槓桿(已經過 leverage.py 與風控上限裁決)
        risk_per_trade_pct  這一筆最多可以虧掉權益的百分之幾

    回傳 SizingResult。approved=False 時 size_usdt 是 None。
    """
    reasons = []
    adjustments = []

    if equity is None or equity <= 0:
        return SizingResult(False, reasons=[f"帳戶權益無效: {equity}"])

    if stop_distance_pct is None or stop_distance_pct <= 0:
        return SizingResult(
            False,
            reasons=[
                f"停損距離無效: {stop_distance_pct}。"
                "沒有停損距離就無法反推倉位大小,這種單一律不開。"
            ],
        )

    if leverage is None or leverage <= 0:
        return SizingResult(False, reasons=[f"槓桿無效: {leverage}"])

    if risk_per_trade_pct is None or risk_per_trade_pct <= 0:
        return SizingResult(False, reasons=[f"單筆風險上限無效: {risk_per_trade_pct}"])

    # ---- 1. 這一筆最多可以虧多少 ----
    risk_usdt = float(equity) * float(risk_per_trade_pct) / 100.0

    # ---- 2. 由停損距離反推名目價值 ----
    # 觸及停損時的虧損 = 名目 × 停損距離%
    # 所以 名目 = 風險金額 ÷ 停損距離%
    notional = risk_usdt / (float(stop_distance_pct) / 100.0)

    # ---- 3. 名目上限:單筆不得超過總曝險限制 ----
    if max_exposure_pct is not None:
        max_total_exposure = float(equity) * float(max_exposure_pct) / 100.0
        room = max_total_exposure - float(current_exposure_usdt or 0)

        if room <= 0:
            return SizingResult(
                False,
                reasons=[
                    f"目前曝險 {current_exposure_usdt:.2f} 已達上限 "
                    f"{max_total_exposure:.2f}({max_exposure_pct}% of {equity:.2f})"
                ],
            )

        if notional > room:
            adjustments.append(
                f"名目 {notional:.2f} 縮到剩餘曝險額度 {room:.2f}"
            )
            notional = room

    if max_notional is not None and notional > max_notional:
        adjustments.append(f"名目 {notional:.2f} 縮到上限 {max_notional:.2f}")
        notional = float(max_notional)

    # ---- 4. 由槓桿換算保證金 ----
    size_usdt = notional / float(leverage)

    # ---- 5. 保證金不得超過可用餘額 ----
    if available_balance is not None and size_usdt > available_balance:
        if available_balance <= 0:
            return SizingResult(
                False, reasons=[f"可用餘額不足: {available_balance}"]
            )
        adjustments.append(
            f"保證金 {size_usdt:.2f} 縮到可用餘額 {available_balance:.2f}"
        )
        size_usdt = float(available_balance)
        notional = size_usdt * float(leverage)

    # ---- 6. 最小名目:低於交易所門檻就不開,不要硬補 ----
    if min_notional is not None and notional < min_notional:
        return SizingResult(
            False,
            reasons=[
                f"名目 {notional:.2f} 低於交易所最小值 {min_notional}。"
                "硬補到最小值會讓這一筆的風險超過設定上限,所以不開。"
            ],
            adjustments=adjustments,
        )

    # 名目被縮過的話,實際風險也跟著變小 —— 重新計算回報實際值
    actual_risk = notional * float(stop_distance_pct) / 100.0

    return SizingResult(
        approved=True,
        size_usdt=round(size_usdt, 8),
        notional=round(notional, 8),
        risk_usdt=round(actual_risk, 8),
        risk_pct_of_equity=round(actual_risk / float(equity) * 100, 4),
        adjustments=adjustments,
    )


def risk_of_position(size_usdt, leverage, stop_distance_pct):
    """已知倉位反推風險金額。給既有部位的風險彙總用。"""
    if None in (size_usdt, leverage, stop_distance_pct):
        return None
    return float(size_usdt) * float(leverage) * float(stop_distance_pct) / 100.0

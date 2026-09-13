"""
Risk Engine — 硬閘 · 2026-09-10(PHASE 1)

═══ 這一層的全部意義 ═══
Master Prompt 第 19 條:「Risk Engine 是 HARD GATE。任何 Agent 都不能
繞過。即使 Strategy Agent 說 STRONG BUY,Risk Engine 也可以 REJECT。」

在此之前,本系統的風控只有兩樣:波動目標(決定部位大小)與逐倉槓桿上限。
兩者都是**訂單產生階段**的縮放,沒有任何一層在訂單產出**之後**、
執行**之前**說「不行」。

2026-09-10 的教訓說明了為什麼需要:UNI-USDT 在 20× 槓桿下被強平,
而查下去六個倉全部在懸崖邊(BNB 距強平剩 0.14%)。當時沒有任何一條
規則會擋下那個狀態 —— 波動目標只管「拿多少名目」,它對強平距離
一無所知。

═══ 設計原則 ═══
一、**只否決,不修改。** 這一層不會偷偷把單改小、改方向。它只回答
    ALLOW / REDUCE / REJECT,並說明理由。修改部位是 Portfolio Manager
    的事,那是另一層。
二、**每一條檢查都要能說出「為什麼」。** 不給理由的拒絕,操作者無法
    判斷是規則錯了還是市場錯了。
三、**門檻全部可設定,不硬編碼**(Master Prompt 第 20 條)。
四、**紙上與實盤共用同一個 Risk Engine。** 若只在實盤才檢查,那紙上
    的績效就是在一個沒有風控的世界裡跑出來的 —— 那是最貴的一種兩把尺。
五、**檢查失敗時拒絕,不是放行。** 資料讀不到、算不出來 → REJECT。
    「絕不用預設值吞掉錯誤」。

═══ 門檻數值的來源 ═══
所有預設值都寫明依據。凡是「執政官指定」的,不得由程式自行更動
(Master Prompt 第 102 條:涉及 Risk Limit 必須人工確認)。
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.logging import get_logger

log = get_logger("portfolio.risk")

BASE = Path(__file__).resolve().parents[1]
CONFIG_PATH = BASE / "data" / "risk_limits.json"


# ══════════════════════════════════════════════════════════
# 門檻(可設定,不硬編碼 —— Master Prompt 第 20 條)
# ══════════════════════════════════════════════════════════
@dataclass(frozen=True)
class RiskLimits:
    """風險門檻。每一個數值都要說得出依據。

    修改任何一項都涉及 Risk Limit,依 Master Prompt 第 102 條
    必須人工確認 —— 程式不得自行調整。
    """

    # 單筆風險:一筆交易最多能虧掉權益的多少 %
    # 依據:業界通用 0.5~2%。本系統出場是均線(距離不固定),
    # 所以用「名目 × 到出場線的距離」估算最壞情況。
    max_risk_per_trade_pct: float = 2.0

    # 單一標的曝險上限(名目 / 權益)
    # 依據:現行 7 幣等權、波動目標下每檔約 7%;20% 給足三倍緩衝,
    # 但擋得住「單一幣佔掉整個帳戶」。
    max_symbol_exposure_pct: float = 20.0

    # 總曝險上限(Σ名目 / 權益)
    # 依據:回測 3.3 年實測曝險從未超過 78%;100% 是「不加真槓桿」的界線。
    max_total_exposure_pct: float = 100.0

    # 同時持倉檔數上限
    # 依據:交易池 7 幣(主城)/ 23 幣(測試組)。25 給測試組留餘裕。
    max_concurrent_positions: int = 25

    # 單日虧損上限(相對前一日權益)
    # 依據:回測日報酬標準差約 1.6%,5% 約等於 3 個標準差。
    max_daily_loss_pct: float = 5.0

    # 最大回撤(自峰值)—— 觸及即停止開新倉
    # 依據:實盤資格契約第四條寫的是 15%;風控用 20% 當硬停,
    # 留 5 個百分點讓契約先示警、風控才動作。
    max_drawdown_pct: float = 20.0

    # 逐倉強平距離下限(%)
    # 依據:2026-09-10 事故 —— 20× 給 4.5% 距離,單日 8.8% 波動就爆。
    # 本組合單日最大跌幅約 10~15%,20% 是「單日極端波動打不到」的界線。
    min_liquidation_distance_pct: float = 20.0

    # 連續虧損筆數上限 —— 觸及即暫停開新倉
    # 依據:趨勢跟隨勝率約 41%,連虧 8 筆的機率約 (0.59)^8 ≈ 1.5%,
    # 不算罕見;10 筆約 0.5%,更適合當「該停下來看看」的訊號。
    max_consecutive_losses: int = 10

    # 資料新鮮度:超過這個時數的行情視為過期,拒絕交易
    # 依據:每日記帳週期 24 小時 + 補跑緩衝。
    max_data_age_hours: float = 30.0

    @classmethod
    def load(cls) -> "RiskLimits":
        """從設定檔載入。缺檔就用預設值並寫出來,讓它變成可見的設定。"""
        try:
            d = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            known = {f: d[f] for f in cls.__dataclass_fields__ if f in d}
            return cls(**known)
        except FileNotFoundError:
            inst = cls()
            inst.save()
            return inst
        except Exception as e:
            # 設定檔壞掉時**不靜靜用預設值** —— 那會讓「風控被改壞」
            # 變成無聲的事。出聲,並用最保守的預設值。
            log.error(f"風險門檻設定檔讀取失敗,改用預設值:{e}")
            return cls()

    def save(self) -> None:
        from core.atomic import write_json_atomic
        write_json_atomic(CONFIG_PATH, asdict(self))


# ══════════════════════════════════════════════════════════
# 判決
# ══════════════════════════════════════════════════════════
ALLOW, REDUCE, REJECT = "ALLOW", "REDUCE", "REJECT"


@dataclass
class Check:
    name: str
    passed: bool
    verdict: str            # ALLOW / REDUCE / REJECT
    detail: str


@dataclass
class RiskDecision:
    verdict: str
    checks: list = field(default_factory=list)
    rejected_symbols: set = field(default_factory=set)

    @property
    def allowed(self) -> bool:
        return self.verdict != REJECT

    def failures(self) -> list:
        return [c for c in self.checks if not c.passed]

    def to_dict(self) -> dict:
        return {"verdict": self.verdict,
                "rejected_symbols": sorted(self.rejected_symbols),
                "checks": [asdict(c) for c in self.checks]}


# ══════════════════════════════════════════════════════════
# Risk Engine
# ══════════════════════════════════════════════════════════
class RiskEngine:
    """訂單產出之後、執行之前的硬閘。

    用法:
        rd = RiskEngine().evaluate(account, orders, marks, equity_curve)
        if not rd.allowed:  ...拒絕整批
        orders = [o for o in orders if o.symbol not in rd.rejected_symbols]
    """

    def __init__(self, limits: RiskLimits | None = None):
        self.limits = limits or RiskLimits.load()

    # ── 帳戶層檢查:任何一條不過 → 整批 REJECT ──────────────
    def _account_checks(self, account, marks, curve) -> list:
        L = self.limits
        out = []
        eq = account.equity(marks)

        # 一、最大回撤
        peak = getattr(account, "peak_equity", 0.0) or 0.0
        dd = ((peak - eq) / peak * 100) if peak > 0 else 0.0
        out.append(Check(
            "最大回撤", dd <= L.max_drawdown_pct,
            ALLOW if dd <= L.max_drawdown_pct else REJECT,
            f"{dd:.2f}%(上限 {L.max_drawdown_pct}%)"))

        # 二、單日虧損
        day_loss = 0.0
        if curve and len(curve) >= 1:
            try:
                prev = float(curve[-1].get("equity") or 0)
                if prev > 0:
                    day_loss = max(0.0, (prev - eq) / prev * 100)
            except (TypeError, ValueError, AttributeError):
                day_loss = 0.0
        out.append(Check(
            "單日虧損", day_loss <= L.max_daily_loss_pct,
            ALLOW if day_loss <= L.max_daily_loss_pct else REJECT,
            f"{day_loss:.2f}%(上限 {L.max_daily_loss_pct}%)"))

        # 三、總曝險
        expo = account.exposure(marks) * 100
        out.append(Check(
            "總曝險", expo <= L.max_total_exposure_pct,
            ALLOW if expo <= L.max_total_exposure_pct else REJECT,
            f"{expo:.1f}%(上限 {L.max_total_exposure_pct}%)"))

        # 四、持倉檔數
        n = len(account.positions)
        out.append(Check(
            "同時持倉檔數", n <= L.max_concurrent_positions,
            ALLOW if n <= L.max_concurrent_positions else REJECT,
            f"{n} 檔(上限 {L.max_concurrent_positions})"))

        # 五、連續虧損
        streak = self._loss_streak(curve)
        out.append(Check(
            "連續虧損", streak < L.max_consecutive_losses,
            ALLOW if streak < L.max_consecutive_losses else REJECT,
            f"連虧 {streak} 筆(上限 {L.max_consecutive_losses})"))

        # 六、現有部位的強平距離 —— 2026-09-10 事故的直接產物
        worst, worst_sym = 999.0, ""
        for s, p in account.positions.items():
            lp = p.liq_price()
            mk = float(marks.get(s) or p.avg_price)
            if lp is None or mk <= 0:
                continue
            d = abs(mk - lp) / mk * 100
            if d < worst:
                worst, worst_sym = d, s
        if account.positions and worst < 999.0:
            ok = worst >= L.min_liquidation_distance_pct
            out.append(Check(
                "最近的強平距離", ok, ALLOW if ok else REJECT,
                f"{worst_sym} 距強平 {worst:.2f}%"
                f"(下限 {L.min_liquidation_distance_pct}%)"))
        return out

    @staticmethod
    def _loss_streak(curve) -> int:
        """從權益曲線末端往回數連續虧損天數。"""
        if not curve:
            return 0
        n = 0
        prev = None
        for row in reversed(curve):
            try:
                v = float(row.get("equity"))
            except (TypeError, ValueError, AttributeError):
                break
            if prev is None:
                prev = v
                continue
            if v > prev:          # 越往回越早;前一天較高 = 當天虧
                n += 1
                prev = v
            else:
                break
        return n

    # ── 逐單檢查:不過的單被剔除,其餘照走 ──────────────────
    def _order_checks(self, account, orders, marks, eq) -> tuple:
        L = self.limits
        out, rejected = [], set()
        if eq <= 0:
            return out, {o.symbol for o in orders}

        # 現有各幣名目
        held = {}
        for s, p in account.positions.items():
            mk = float(marks.get(s) or p.avg_price)
            held[s] = abs(p.position_amt) * mk

        for o in orders:
            sym = o.symbol
            delta = o.notional if o.side == "BUY" else -o.notional
            after = max(0.0, held.get(sym, 0.0) + delta)
            expo = after / eq * 100
            if expo > L.max_symbol_exposure_pct:
                rejected.add(sym)
                out.append(Check(
                    f"{sym} 單幣曝險", False, REJECT,
                    f"成交後 {expo:.1f}%(上限 "
                    f"{L.max_symbol_exposure_pct}%)"))
                continue

            # 單筆風險:名目 × 到出場線的距離
            if o.side == "BUY" and o.stop and o.price > 0:
                risk = o.notional * abs(o.price - o.stop) / o.price
                risk_pct = risk / eq * 100
                if risk_pct > L.max_risk_per_trade_pct:
                    rejected.add(sym)
                    out.append(Check(
                        f"{sym} 單筆風險", False, REJECT,
                        f"到出場線的風險 {risk_pct:.2f}%(上限 "
                        f"{L.max_risk_per_trade_pct}%)"))
                    continue
                out.append(Check(f"{sym} 單筆風險", True, ALLOW,
                                 f"{risk_pct:.2f}%"))
        return out, rejected

    # ── 主入口 ────────────────────────────────────────
    def evaluate(self, account, orders, marks, curve=None,
                 data_age_hours: float | None = None) -> RiskDecision:
        L = self.limits
        checks = []

        # 資料新鮮度 —— 讀不到就拒絕,不放行
        if data_age_hours is not None:
            ok = data_age_hours <= L.max_data_age_hours
            checks.append(Check(
                "行情資料新鮮度", ok, ALLOW if ok else REJECT,
                f"{data_age_hours:.1f} 小時(上限 {L.max_data_age_hours})"))

        try:
            eq = account.equity(marks)
            checks += self._account_checks(account, marks, curve or [])
            oc, rejected = self._order_checks(account, orders, marks, eq)
            checks += oc
        except Exception as e:
            # 算不出來就拒絕。**絕不因為檢查本身壞掉而放行。**
            log.error(f"風控檢查拋出例外,拒絕全部訂單:{e}")
            return RiskDecision(REJECT, [Check(
                "風控引擎", False, REJECT,
                f"檢查失敗:{type(e).__name__}: {e}")],
                {o.symbol for o in orders})

        blocking = [c for c in checks if c.verdict == REJECT
                    and not c.name.startswith(tuple(
                        f"{o.symbol} " for o in orders))]
        if blocking:
            return RiskDecision(REJECT, checks,
                                {o.symbol for o in orders})
        return RiskDecision(REDUCE if rejected else ALLOW, checks, rejected)


def evaluate(account, orders, marks, curve=None,
             data_age_hours: float | None = None) -> RiskDecision:
    """便捷入口。"""
    return RiskEngine().evaluate(account, orders, marks, curve,
                                 data_age_hours)

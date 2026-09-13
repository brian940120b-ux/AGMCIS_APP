"""
新系統 — 合約帳本(BingX 規格)· 2026-09-08

═══ 為什麼從「現貨式」改成「合約式」═══
首版的記帳是「拿 USDT 去買幣」:買 707 USDT 的 BTC,現金就少 707。
那是現貨的邏輯。**合約不是這樣運作的。**

合約是:你的 USDT 一直是 USDT,開倉只是**鎖住一部分當保證金**,
盈虧以 USDT 結算。1× 槓桿下兩者的權益數字相同,但有三件事現貨式記法
根本顯示不出來,而它們是合約交易員最先看的:

  · 可用保證金   還能再開多少
  · **強平價**   這一倉離歸零還有多遠
  · 保證金率     >100% 就爆倉

而且未來要接 BingX 實盤時,欄位必須對得上,否則就是「兩把尺」——
紙上帳本說一套、交易所回傳另一套,差異會無聲累積。

═══ 欄位對照 BingX Swap V2(CCXT 對接規格)═══
  帳戶:balance / equity / unrealizedProfit / realisedProfit
        usedMargin / availableMargin
  部位:positionAmt(正=多 負=空)/ avgPrice / markPrice
        unrealizedProfit / liquidationPrice
        initialMargin / maintenanceMargin / marginRatio / leverage

═══ 強平價用標記價判定,不是最新成交價 ═══
交易所就是這樣做的。用最新價會被一根插針掃掉,而標記價有防操縱設計。

═══ 記帳鐵則 ═══
· 每一筆進出都必須有數量與價格,不得只記比例
· 手續費在成交當下扣,資金費每天扣,兩者都直接進 balance
· 權益 = balance + Σ 未實現;未實現不進 balance
· 平倉才實現損益,並記進 realised_pnl
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.atomic import write_json_atomic
from core.logging import get_logger

log = get_logger("portfolio.account")

BASE = Path(__file__).resolve().parents[1]
STATE = BASE / "data" / "portfolio_account.json"
START_BALANCE = 10_000.0

# ── 合約參數(BingX 規格)──────────────────────────────────
# 維持保證金率:API 的 contracts 端點沒有回這個欄位,取 0.5% ——
# BingX 主流幣低槓桿檔位的典型值,而且刻意取偏高的一側:
# 假設要往不利自己的方向錯。
MAINT_MARGIN_RATE = 0.005
DEFAULT_LEVERAGE = 1.0        # 不加槓桿。槓桿是放大器,不是優勢。
# ─────────────────────────────────────────────────────────


@dataclass
class Position:
    """一個合約部位。欄位名對齊 BingX。"""
    symbol: str
    position_amt: float = 0.0       # 正 = 多 / 負 = 空
    avg_price: float = 0.0          # 開倉均價
    leverage: float = DEFAULT_LEVERAGE
    isolated: bool = True           # 逐倉
    opened_at: str = ""
    fee_paid: float = 0.0
    funding_paid: float = 0.0

    @property
    def side(self) -> str:
        return "LONG" if self.position_amt > 0 else (
            "SHORT" if self.position_amt < 0 else "FLAT")

    def notional(self, mark: float) -> float:
        return abs(self.position_amt) * mark

    def initial_margin(self) -> float:
        """起始保證金 = 開倉均價 ÷ 槓桿 × 持倉量。

        ═══ 2026-09-09 修正:原本用標記價算,錯了 ═══
        BingX 原文:「position margin = avg. position price / leverage
        * position size」—— 用的是**開倉均價**,不是標記價。

        道理也對:保證金是開倉當下真的被鎖起來的那筆錢,鎖多少就是多少,
        不會因為之後價格漲跌而變多變少(浮動的是未實現盈虧,不是保證金)。
        用標記價算會讓「已用保證金」每秒都在跳,那是錯的模型。

        這條錯會連帶影響 ROI(分母)、已用/可用保證金、保證金率,
        所以不留相容參數,直接改成不吃 mark —— 讓任何舊呼叫點立刻報錯,
        而不是靜靜算出另一個數字。
        """
        return (abs(self.position_amt) * self.avg_price / self.leverage
                if self.leverage else 0.0)

    def maint_margin(self, mark: float) -> float:
        return self.notional(mark) * MAINT_MARGIN_RATE

    def unrealized(self, mark: float) -> float:
        return (mark - self.avg_price) * self.position_amt

    def liq_price(self) -> float | None:
        """強平價。逐倉。

        BingX 原文(USDⓢ-M 逐倉,無追加保證金):
            多單 = 開倉價 − (起始保證金 − 維持保證金) / 持倉量
            空單 = 開倉價 + (起始保證金 − 維持保證金) / 持倉量

        代入 起始保證金 = 開倉價×量/L、維持保證金 = 開倉價×量×MMR,
        兩邊約掉持倉量後就是下面這個式子 —— 已核對與交易所公式等價:
            多單:入場 × (1 − 1/L + MMR)
            空單:入場 × (1 + 1/L − MMR)
        """
        if self.position_amt == 0 or self.leverage <= 0 or self.avg_price <= 0:
            return None
        move = 1.0 / self.leverage - MAINT_MARGIN_RATE
        return (self.avg_price * (1 - move) if self.position_amt > 0
                else self.avg_price * (1 + move))

    def margin_ratio(self, mark: float) -> float:
        """風險率。>=1(100%)即強平。

        照 BingX 逐倉原文:
            風險 = (維持保證金 + 平倉手續費) / (倉位保證金 + 未實現)

        ═══ 2026-09-09 補:分子原本漏了平倉手續費 ═══
        交易所平你的倉時要收一次手續費,那筆錢也得由保證金出。
        漏算它會讓風險率看起來比實際低 —— 在爆倉這種事情上,
        算得比實際樂觀是最不能接受的方向。
        """
        eq = self.initial_margin() + self.unrealized(mark)
        return ((self.maint_margin(mark) + self.close_fee(mark)) / eq
                if eq > 1e-12 else 999.0)

    def close_fee(self, mark: float) -> float:
        """平倉手續費。吃單費率,取自 costs.py 這個唯一來源。"""
        from portfolio.costs import TAKER_FEE_PCT
        return self.notional(mark) * TAKER_FEE_PCT / 100.0

    def roi(self, mark: float) -> float:
        """報酬率(%)—— BingX 持倉列第一眼在看的數字。

        定義照交易所:未實現 / 起始保證金 × 100%(2026-09-09 查證
        BingX 說明文件「ROI = Unrealized PnL / Initial Margin x 100%」)。

        為什麼要有這一欄:價格漲跌百分比看不出槓桿的效果。
        20× 槓桿下價格只動 1%,投入的保證金就賺賠 20% —— 那才是這筆
        倉位對本金真正的意義。只顯示價格變動百分比會讓人低估風險。
        """
        im = self.initial_margin()
        return (self.unrealized(mark) / im * 100.0) if im > 1e-12 else 0.0


@dataclass
class Account:
    balance: float = START_BALANCE          # 錢包 USDT(不含未實現)
    start_equity: float = START_BALANCE
    peak_equity: float = START_BALANCE
    positions: dict[str, Position] = field(default_factory=dict)
    realized_pnl: float = 0.0
    fee_paid: float = 0.0
    funding_paid: float = 0.0
    days: int = 0
    orders_filled: int = 0
    started_at: str | None = None
    last_signal_day: str | None = None
    bench_start: dict[str, float] = field(default_factory=dict)
    strategy: str = ""
    strategy_fingerprint: str = ""
    updated: str = ""
    # 資金費已經收到哪一個結算時點(毫秒)。用它界定下一次要收的區間,
    # 避免重跑時重複收費、或跨日時漏收 —— 交易所是按結算時點收的,
    # 不是按「天」收的,所以這裡也必須記時點。
    funding_through_ms: int = 0

    # ── 帳戶層(欄位名對齊 BingX)────────────────────────
    def equity(self, marks: dict[str, float]) -> float:
        return self.balance + self.unrealized(marks)

    def unrealized(self, marks: dict[str, float]) -> float:
        return sum(p.unrealized(float(marks.get(s) or p.avg_price))
                   for s, p in self.positions.items())

    def used_margin(self, marks: dict[str, float]) -> float:
        # 起始保證金不隨標記價浮動(開倉當下就鎖定),所以這裡不需要 marks
        return sum(p.initial_margin() for p in self.positions.values())

    def maint_margin(self, marks: dict[str, float]) -> float:
        return sum(p.maint_margin(float(marks.get(s) or p.avg_price))
                   for s, p in self.positions.items())

    def available_margin(self, marks: dict[str, float]) -> float:
        return self.equity(marks) - self.used_margin(marks)

    def exposure(self, marks: dict[str, float]) -> float:
        eq = self.equity(marks)
        if eq <= 0:
            return 0.0
        return sum(p.notional(float(marks.get(s) or p.avg_price))
                   for s, p in self.positions.items()) / eq

    def weights(self, marks: dict[str, float]) -> dict[str, float]:
        """帶正負號的權重:正 = 多、負 = 空。訂單層用它算差額。"""
        eq = self.equity(marks)
        if eq <= 0:
            return {}
        out = {}
        for s, p in self.positions.items():
            if p.position_amt == 0:
                continue
            mk = float(marks.get(s) or p.avg_price)
            out[s] = p.position_amt * mk / eq
        return out

    # ── 成交 ──────────────────────────────────────────
    def fill(self, symbol: str, side: str, qty: float, price: float,
             fee: float, now: str, leverage: float = DEFAULT_LEVERAGE) -> float:
        """記一筆成交。回傳實現損益(開倉/加倉為 0)。

        合約記法:balance 只被手續費與實現損益改變,
        **不因為開倉而減少** —— 開倉只是鎖保證金。

        leverage 是**交易所端設定的槓桿**(像真實下單前要先設槓桿一樣),
        不是每筆單各算一次。策略透過波動目標把總曝險縮放到
        0~leverage_cap 之間,槓桿設定本身固定,保證金隨曝險變動 ——
        這與 BingX 的實際運作一致:你先設槓桿,用多用少的是保證金佔用。
        """
        self.balance -= fee
        self.fee_paid += fee
        self.orders_filled += 1
        p = self.positions.get(symbol) or Position(
            symbol=symbol, leverage=leverage)
        p.leverage = leverage
        signed = qty if side == "BUY" else -qty
        realized = 0.0

        if p.position_amt == 0 or (p.position_amt > 0) == (signed > 0):
            # 開倉 / 同向加倉 → 更新均價
            total = p.avg_price * abs(p.position_amt) + price * abs(signed)
            p.position_amt += signed
            p.avg_price = total / abs(p.position_amt) if p.position_amt else price
            if not p.opened_at:
                p.opened_at = now
        else:
            # 反向 → 先平倉,平完還有剩就反手
            close_qty = min(abs(signed), abs(p.position_amt))
            direction = 1 if p.position_amt > 0 else -1
            realized = (price - p.avg_price) * close_qty * direction
            self.realized_pnl += realized
            self.balance += realized
            p.position_amt += signed
            if abs(p.position_amt) < 1e-12:
                p.position_amt = 0.0
                p.fee_paid += fee
                self.positions.pop(symbol, None)
                return realized
            if (p.position_amt > 0) != (direction > 0):
                p.avg_price = price          # 反手,均價重設
                p.opened_at = now
        p.fee_paid += fee
        self.positions[symbol] = p
        return realized

    def charge_funding(self, marks: dict[str, float],
                       daily_rates: dict[str, float]) -> float:
        """收一日資金費。做多付、做空收 —— 符號跟著部位方向走。

        ═══ BingX 規格 ═══
        資金費 = 倉位名目 × 資金費率,每 8 小時結算一次
        (00:00 / 08:00 / 16:00 UTC)。本系統每日記帳一次,
        所以傳進來的是「當日三次結算合計」的日費率。

        ═══ 2026-09-09 修正:改成逐幣費率 ═══
        原本吃單一個 float(全市場中位數),但交易所是**每個幣收自己的
        費率**,而實測差距很大:UNI 年化 3.61%、BNB/XRP 10.95%,
        近三倍。用中位數等於對貴的幣少收、對便宜的幣多收。
        改成 dict 而不是留 float 相容 —— 舊呼叫點會立刻 TypeError,
        不會靜靜用錯的數字繼續跑。
        """
        total = 0.0
        for s, p in self.positions.items():
            mk = float(marks.get(s) or p.avg_price)
            rate = float(daily_rates.get(s, 0.0))
            f = p.position_amt * mk * rate           # 空單為負 = 收錢
            p.funding_paid += f
            total += f
        self.balance -= total
        self.funding_paid += total
        return total

    def liquidation_check(self, marks: dict[str, float]) -> list[str]:
        """回傳已觸及強平價的部位(以標記價判定)。**只查不平。**

        要真的平倉請用 liquidate()。分開的理由是這支也被面板與即時層
        呼叫,而那兩層一律唯讀。
        """
        hit = []
        for s, p in self.positions.items():
            lp = p.liq_price()
            mk = marks.get(s)
            if lp is None or mk is None:
                continue
            if (p.position_amt > 0 and mk <= lp) or \
               (p.position_amt < 0 and mk >= lp):
                hit.append(s)
        return hit

    def liquidate(self, symbol: str, now: str) -> dict:
        """**真的執行強平** —— 照交易所的做法把部位強制平掉。

        ═══ 2026-09-10:為什麼補這個 ═══
        原本 paper.tick() 偵測到強平只寫一行 log,**部位照樣留著繼續虧**。
        2026-09-10 00:30 UNI-USDT 真的觸發了:標記價 6.155 已跌破強平價
        6.44434,而帳本讓它一路虧到 −61.67,遠超過投入的保證金 35.09
        (ROI −175%)。在真實交易所,那一倉在 6.444 就被平掉,損失鎖定
        在保證金;我們的帳本卻假裝什麼都沒發生。
        **從那一刻起,帳本與交易所會做的事已經永久分岔** —— 而且如果
        價格反彈,帳本還會「賺回來」,那在現實中不可能發生。

        逐倉強平的結算方式:
          · 成交價 = 強平價(不是當前標記價 —— 交易所在觸價當下就平了)
          · 平倉手續費照收(吃單費率)
          · 部位歸零,損失落進 realized_pnl
        逐倉的意義是損失止於該倉保證金,不會波及其他部位。
        """
        p = self.positions.get(symbol)
        if p is None or p.position_amt == 0:
            return {"symbol": symbol, "skipped": "無部位"}
        lp = p.liq_price()
        if lp is None:
            return {"symbol": symbol, "skipped": "無強平價"}

        amt = p.position_amt
        margin = p.initial_margin()
        # 在強平價平倉:多單賣出、空單買回
        realized = (lp - p.avg_price) * amt
        fee = abs(amt) * lp * (self._taker_pct() / 100.0)
        self.realized_pnl += realized
        self.balance += realized - fee
        self.fee_paid += fee
        self.orders_filled += 1
        del self.positions[symbol]
        log.warning(
            f"**強平執行** {symbol}:於 {lp:.8g} 平掉 {amt:+.8g},"
            f"實現 {realized:+.4f}、手續費 {fee:.4f}、原保證金 {margin:.4f}")
        return {"symbol": symbol, "liq_price": lp, "qty": amt,
                "realized": realized, "fee": fee, "margin_lost": margin,
                "at": now}

    @staticmethod
    def _taker_pct() -> float:
        from portfolio.costs import TAKER_FEE_PCT
        return TAKER_FEE_PCT

    # ── 存讀 ──────────────────────────────────────────
    def to_dict(self, marks: dict[str, float] | None = None) -> dict:
        marks = marks or {}
        eq = self.equity(marks)
        pos = {}
        for s, p in self.positions.items():
            mk = float(marks.get(s) or p.avg_price)
            pos[s] = {
                **asdict(p),
                "mark_price": round(mk, 10),
                "notional": round(p.notional(mk), 6),
                "unrealized_pnl": round(p.unrealized(mk), 6),
                "initial_margin": round(p.initial_margin(), 6),
                "close_fee": round(p.close_fee(mk), 6),
                "maint_margin": round(p.maint_margin(mk), 6),
                "liq_price": (round(p.liq_price(), 10)
                              if p.liq_price() is not None else None),
                "margin_ratio": round(p.margin_ratio(mk), 6),
                "roi_pct": round(p.roi(mk), 6),
                "side": p.side,
            }
        d = {k: v for k, v in asdict(self).items() if k != "positions"}
        d.update({
            "positions": pos,
            "equity": round(eq, 6),
            "unrealized_pnl": round(self.unrealized(marks), 6),
            "used_margin": round(self.used_margin(marks), 6),
            "available_margin": round(self.available_margin(marks), 6),
            "maint_margin": round(self.maint_margin(marks), 6),
            "exposure": round(self.exposure(marks), 6),
            "return_pct": round(eq / self.start_equity * 100 - 100, 6),
            "drawdown_pct": round(
                max(0.0, (self.peak_equity - eq) / self.peak_equity * 100), 6)
            if self.peak_equity > 0 else 0.0,
            "maint_margin_rate": MAINT_MARGIN_RATE,
            "leverage": max((p.leverage for p in self.positions.values()),
                            default=DEFAULT_LEVERAGE),
            # BingX 原文:Realized PnL = 已平倉損益 − 交易手續費 − 資金費。
            # 我們原本三個數字分開存(方便追成本來源),但交易所給使用者
            # 看的是扣完的淨額 —— 兩個都留:分項用來診斷,淨額用來對帳。
            "realized_pnl_net": round(
                self.realized_pnl - self.fee_paid - self.funding_paid, 6),
        })
        return d

    def save(self, marks: dict[str, float] | None = None,
             path: Path | None = None) -> None:
        """存檔。path 讓測試組用自己的帳本,預設是主城的。

        2026-09-10 加 path:測試組必須有**獨立帳本**,但**共用同一份
        Account 邏輯** —— 複製一份 account.py 就是憲法裡犯了十一次的
        「兩把尺」。參數化不是為了彈性,是為了不要有第二份實作。
        """
        write_json_atomic(path or STATE, self.to_dict(marks or {}))

    @classmethod
    def load(cls, path: Path | None = None) -> "Account":
        try:
            d = json.loads((path or STATE).read_text(encoding="utf-8"))
        except Exception:
            return cls(started_at=datetime.now(timezone.utc).isoformat())
        pos: dict[str, Position] = {}
        for s, v in (d.get("positions") or {}).items():
            if not isinstance(v, dict):
                continue
            # 舊格式(qty/entry)自動遷移到合約格式
            amt = v.get("position_amt")
            if amt is None:
                amt = float(v.get("qty") or 0)
            pos[s] = Position(
                symbol=s, position_amt=float(amt),
                avg_price=float(v.get("avg_price") or v.get("entry") or 0),
                leverage=float(v.get("leverage") or DEFAULT_LEVERAGE),
                isolated=bool(v.get("isolated", True)),
                opened_at=v.get("opened_at", ""),
                fee_paid=float(v.get("fee_paid") or 0),
                funding_paid=float(v.get("funding_paid") or 0))
        # 舊格式的 cash 就是合約格式的 balance 加上持倉成本
        bal = d.get("balance")
        if bal is None:
            cash = float(d.get("cash") or START_BALANCE)
            bal = cash + sum(abs(p.position_amt) * p.avg_price
                             for p in pos.values())
            log.info("帳本由現貨式遷移到合約式:"
                     f"cash {cash:,.2f} → balance {bal:,.2f}")
        return cls(
            balance=float(bal),
            start_equity=float(d.get("start_equity") or START_BALANCE),
            peak_equity=float(d.get("peak_equity") or START_BALANCE),
            positions=pos,
            realized_pnl=float(d.get("realized_pnl") or 0),
            fee_paid=float(d.get("fee_paid") or 0),
            funding_paid=float(d.get("funding_paid") or 0),
            days=int(d.get("days") or 0),
            orders_filled=int(d.get("orders_filled") or 0),
            started_at=d.get("started_at"),
            last_signal_day=d.get("last_signal_day"),
            bench_start=d.get("bench_start") or {},
            strategy=d.get("strategy", ""),
            strategy_fingerprint=d.get("strategy_fingerprint", ""),
            updated=d.get("updated", ""),
            funding_through_ms=int(d.get("funding_through_ms") or 0))

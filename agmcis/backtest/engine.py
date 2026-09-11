"""
回測引擎。

Phase 0 稽核在舊回測找到的偏誤,這裡逐一修掉:

  1. **停損停利只比對收盤價。**
     盤中穿刺停損的 K 棒不會被判定停損,勝率被系統性高估。
     -> 現在用 high / low 判定。

  2. **訊號與成交發生在同一根 K 棒。**
     用收盤價產生訊號,又用同一根的收盤價成交,等於偷看了未來。
     -> 訊號在第 i 根收盤產生,成交在第 i+1 根的**開盤價**。

  3. **完全沒有成本模型。**
     -> 手續費、滑點、點差、資金費用、清算費全部納入。

  4. **只做多。**
     -> 做多做空對等。

  5. **沒有強平。**
     -> 每根 K 棒用 high/low 檢查強平。停損與強平之中,**離進場價較近的那個先觸發**
        —— 不是無條件先檢查強平。槓桿上限也與 Risk Engine 共用同一支函式,
        避免回測模擬出風控不會放行的部位。

同根 K 棒同時觸及停損與停利時的處理:
    沒有逐筆成交資料就無法知道誰先到。**一律假設停損先到。**
    這是保守假設 —— 寧可低估績效,也不要高估。
    另一種做法(假設停利先到)會讓回測結果系統性偏樂觀,那正是要避免的事。
"""
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from agmcis.backtest.costs import DEFAULT_COSTS, CostModel
from agmcis.core.enums import Direction
from agmcis.risk.leverage import max_leverage_for_stop

logger = logging.getLogger("agmcis.backtest")

# 強平時保證金大約剩多少比例。BingX 的實際維持保證金率依合約而異,
# 這裡用保守值:虧掉 90% 保證金就當作被強平。
MAINTENANCE_MARGIN_RATIO = 0.10


@dataclass
class BacktestTrade:
    symbol: str
    direction: Direction
    entry_index: int
    entry_time: int
    entry_price: float
    size_usdt: float            # 保證金
    leverage: float
    quantity: float
    stop_loss: float
    take_profit: Optional[float] = None

    exit_index: Optional[int] = None
    exit_time: Optional[int] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None

    gross_pnl: float = 0.0
    fees: float = 0.0
    funding: float = 0.0
    net_pnl: float = 0.0

    # 持倉期間最大浮盈 / 最大浮虧(佔保證金的百分比)
    mfe_pct: float = 0.0
    mae_pct: float = 0.0

    @property
    def notional(self):
        return self.size_usdt * self.leverage

    @property
    def is_open(self):
        return self.exit_index is None

    @property
    def is_win(self):
        return self.net_pnl > 0

    @property
    def bars_held(self):
        if self.exit_index is None:
            return 0
        return self.exit_index - self.entry_index

    @property
    def return_pct(self):
        """保證金報酬率。"""
        if self.size_usdt <= 0:
            return 0.0
        return self.net_pnl / self.size_usdt * 100

    @property
    def r_multiple(self):
        """賺賠倍數:實際損益 ÷ 原本打算冒的風險。"""
        risk = abs(self.entry_price - self.stop_loss) / self.entry_price * self.notional
        if risk <= 0:
            return None
        return round(self.net_pnl / risk, 3)

    def to_dict(self):
        return {
            "symbol": self.symbol,
            "direction": self.direction.value,
            "entry_index": self.entry_index,
            "entry_time": self.entry_time,
            "entry_price": round(self.entry_price, 8),
            "exit_index": self.exit_index,
            "exit_time": self.exit_time,
            "exit_price": round(self.exit_price, 8) if self.exit_price else None,
            "exit_reason": self.exit_reason,
            "size_usdt": round(self.size_usdt, 4),
            "leverage": self.leverage,
            "notional": round(self.notional, 4),
            "gross_pnl": round(self.gross_pnl, 4),
            "fees": round(self.fees, 4),
            "funding": round(self.funding, 4),
            "net_pnl": round(self.net_pnl, 4),
            "return_pct": round(self.return_pct, 4),
            "r_multiple": self.r_multiple,
            "bars_held": self.bars_held,
            "mfe_pct": round(self.mfe_pct, 4),
            "mae_pct": round(self.mae_pct, 4),
        }


@dataclass
class BacktestResult:
    trades: List[BacktestTrade] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)
    start_balance: float = 10000.0
    end_balance: float = 10000.0
    bars: int = 0
    # 訊號產生了但被拒絕(停損方向錯、名目太小等)
    skipped_signals: int = 0
    # 已有部位所以根本沒去問策略的 K 棒數
    signals_while_in_position: int = 0
    warnings: List[str] = field(default_factory=list)
    cost_model: Optional[CostModel] = None

    @property
    def closed_trades(self):
        return [t for t in self.trades if not t.is_open]


class BacktestEngine:
    """
    逐根 K 棒推進的回測。

    重要:這個引擎一次只持有一個部位。多部位與投資組合層級的回測
    需要不同的資金分配邏輯,排在後面的 Phase。
    """

    def __init__(self, costs=None, start_balance=10000.0,
                 risk_per_trade_pct=1.0, max_leverage=5.0,
                 maintenance_margin_ratio=MAINTENANCE_MARGIN_RATIO,
                 candle_hours=1.0):
        self.costs = costs if costs is not None else DEFAULT_COSTS
        self.start_balance = float(start_balance)
        self.risk_per_trade_pct = float(risk_per_trade_pct)
        self.max_leverage = float(max_leverage)
        self.maintenance_margin_ratio = float(maintenance_margin_ratio)
        self.candle_hours = float(candle_hours)

    # ---------------- 主迴圈 ----------------

    def run(self, candles, signal_fn, symbol="BACKTEST", warmup=60, exit_fn=None):
        """
        candles: [{time, open, high, low, close, volume}, ...] 或等價的 list of list。

        signal_fn(history, index) -> dict | None
            history 是**到第 index 根為止(含)**的 K 棒。
            回傳 {"direction", "stop_loss", "take_profit"} 或 None。

            ⚠️ signal_fn 拿到的 history 絕對不包含第 index+1 根之後的資料。
            這是引擎保證的,也是不偷看未來的關鍵。

        exit_fn(history, index, position) -> bool
            策略自己決定要出場(不是停損也不是停利)。
            與進場同一條規則:**第 i 根收盤決定,第 i+1 根開盤成交**。
            舊回測用同一根的收盤價出場,那同樣是偷看未來。
        """
        rows = [_normalise(c) for c in candles]
        result = BacktestResult(
            start_balance=self.start_balance,
            end_balance=self.start_balance,
            bars=len(rows),
            cost_model=self.costs,
        )

        if len(rows) <= warmup + 1:
            result.warnings.append(
                f"K 棒只有 {len(rows)} 根,暖機需要 {warmup} 根,無法回測"
            )
            result.equity_curve = [self.start_balance]
            return result

        balance = self.start_balance
        equity_curve = [balance]
        position = None
        pending = None        # 上一根產生、這一根要成交的進場訊號
        pending_exit = False  # 上一根策略喊出場、這一根開盤成交

        for index in range(warmup, len(rows)):
            candle = rows[index]

            # ---- 1. 先處理已有部位的出場(用這根的 high/low)----
            if position is not None:
                exited = self._check_exit(position, candle, index)
                if exited:
                    balance += position.net_pnl
                    result.trades.append(position)
                    position = None
                    pending_exit = False
                    equity_curve.append(round(balance, 8))

            # ---- 1b. 上一根策略喊的出場,在這一根開盤成交 ----
            #     停損停利優先於策略出場 —— 部位已經被停掉就沒得出場了。
            if position is not None and pending_exit:
                self._close(position, candle["open"], candle, index, "策略出場")
                balance += position.net_pnl
                result.trades.append(position)
                position = None
                pending_exit = False
                equity_curve.append(round(balance, 8))

            # ---- 2. 再處理上一根掛著的進場訊號 ----
            #    成交價是**這一根的開盤價**,不是上一根的收盤價。
            if pending is not None and position is None:
                position = self._open_position(
                    pending, candle, index, balance, symbol,
                )
                if position is None:
                    # 訊號被拒(停損方向錯、名目太小等)
                    result.skipped_signals += 1
                pending = None

            # ---- 3. 最後才產生新訊號 ----
            #    signal_fn 只看得到 rows[:index+1],看不到未來。
            #    已有部位時不呼叫 —— 這個引擎一次只持有一個部位,
            #    問了也不能用。加倉與反手要等多部位版本。
            if position is None:
                signal = signal_fn(rows[: index + 1], index)
                if signal:
                    pending = signal
            else:
                result.signals_while_in_position += 1
                if exit_fn is not None and not pending_exit:
                    if exit_fn(rows[: index + 1], index, position):
                        pending_exit = True

        # 收盤時強制平倉,否則未實現損益會被忽略
        if position is not None:
            self._force_close(position, rows[-1], len(rows) - 1, "回測結束")
            balance += position.net_pnl
            result.trades.append(position)
            equity_curve.append(round(balance, 8))

        result.equity_curve = equity_curve
        result.end_balance = round(balance, 8)
        return result

    # ---------------- 進場 ----------------

    def _open_position(self, signal, candle, index, balance, symbol):
        direction = Direction.parse(signal.get("direction"))
        if direction is None or not direction.is_directional:
            return None

        stop_loss = signal.get("stop_loss")
        if stop_loss is None:
            return None

        # 成交價 = 這一根的開盤價 + 成本
        raw_price = candle["open"]
        is_long = direction is Direction.LONG
        entry_price = self.costs.entry_price(raw_price, is_long)

        # 停損必須在正確方向,否則這張單一開就會被停掉
        if is_long and stop_loss >= entry_price:
            return None
        if not is_long and stop_loss <= entry_price:
            return None

        stop_distance_pct = abs(entry_price - stop_loss) / entry_price * 100
        if stop_distance_pct <= 0:
            return None

        # 倉位由風險反推,與 Risk Engine 同一套公式
        risk_usdt = balance * self.risk_per_trade_pct / 100
        notional = risk_usdt / (stop_distance_pct / 100)

        # 槓桿上限直接用 Risk Engine 那支函式,不另外寫一套。
        #
        # 第一版我在這裡自己算 (1 - mmr) * 100 / stop%,結果強平價**剛好等於**
        # 停損價 —— 邊界上兩者同時觸發,誰先到取決於浮點誤差。
        # Risk Engine 的 LIQUIDATION_SAFETY_FACTOR 本來就是為了這件事存在的,
        # 而且回測若允許風控不會放行的槓桿,結果就沒有參考價值。
        lev_cap = max_leverage_for_stop(stop_distance_pct)
        leverage = min(self.max_leverage, lev_cap) if lev_cap else self.max_leverage
        leverage = max(1.0, leverage)

        # 停損距離大到連 1x 都會先被強平(停損距離 >= 90%)。
        # 這種部位風控不會放行,回測也不該假裝做得到。
        liquidation_move = (1 - self.maintenance_margin_ratio) / leverage
        if stop_distance_pct / 100 >= liquidation_move:
            return None

        size_usdt = notional / leverage
        if size_usdt > balance:
            size_usdt = balance
            notional = size_usdt * leverage

        if notional <= 0 or size_usdt <= 0:
            return None

        quantity = notional / entry_price
        entry_fee = self.costs.fee(notional)

        position = BacktestTrade(
            symbol=symbol,
            direction=direction,
            entry_index=index,
            entry_time=candle["time"],
            entry_price=entry_price,
            size_usdt=size_usdt,
            leverage=leverage,
            quantity=quantity,
            stop_loss=float(stop_loss),
            take_profit=signal.get("take_profit"),
        )
        position.fees = entry_fee
        return position

    # ---------------- 出場 ----------------

    def _check_exit(self, position, candle, index):
        """
        用這一根的 high / low 判定出場。

        停損與強平誰先觸發,取決於**哪個價位離進場價比較近** ——
        價格往不利方向移動時,會先經過比較近的那個。

            做多(價格往下):取 max(停損, 強平)
            做空(價格往上):取 min(停損, 強平)

        我第一版寫成無條件「先檢查強平」,那是錯的:
        停損 99、強平 91 的部位在一根跌到 85 的 K 棒裡會被回報成強制平倉,
        但實際上價格是先經過 99 才到 91 的,那筆應該是正常停損出場。
        這個錯誤會讓回測把正常停損誇大成爆倉,反而低估策略 ——
        方向雖然保守,但它是錯的,而且會誤導對槓桿設定的判斷。

        強平真的先發生,只有在它比停損更接近進場價的時候,
        也就是槓桿相對停損距離開太高。

        停損與停利同根時一律假設停損先到(見模組說明)。
        """
        is_long = position.direction is Direction.LONG
        high, low = candle["high"], candle["low"]

        self._update_excursion(position, high, low)

        liquidation_price = self._liquidation_price(position)
        stop_price = position.stop_loss

        # 離進場價較近的那個會先被觸發
        if is_long:
            adverse_level = max(stop_price, liquidation_price)
            adverse_hit = low <= adverse_level
        else:
            adverse_level = min(stop_price, liquidation_price)
            adverse_hit = high >= adverse_level

        if adverse_hit:
            fill = adverse_level
            open_price = candle["open"]

            # 跳空 / 閃崩:開盤價已經穿過觸發價,就不可能在觸發價成交。
            # 用開盤價成交是保守的一邊 —— 假設還能在停損價出場會高估績效。
            if (is_long and open_price < fill) or (not is_long and open_price > fill):
                fill = open_price

            # 成交價已經穿過強平價,這筆就是強平而不是正常停損。
            liquidated = (
                fill <= liquidation_price if is_long else fill >= liquidation_price
            )

            return self._close(
                position, fill, candle, index,
                "強制平倉" if liquidated else "停損",
                liquidated=liquidated,
            )

        # ---- 停利 ----
        if position.take_profit is not None:
            if is_long and high >= position.take_profit:
                return self._close(position, position.take_profit, candle, index, "停利")
            if not is_long and low <= position.take_profit:
                return self._close(position, position.take_profit, candle, index, "停利")

        return False

    def _liquidation_price(self, position):
        """
        大約的強平價。

        保證金虧掉 (1 - 維持保證金率) 時觸發。
        槓桿 N 倍時,價格逆向走 (1 - mmr)/N 就會到。
        """
        move = (1 - self.maintenance_margin_ratio) / position.leverage
        if position.direction is Direction.LONG:
            return position.entry_price * (1 - move)
        return position.entry_price * (1 + move)

    def _update_excursion(self, position, high, low):
        """記錄持倉期間的最大浮盈(MFE)與最大浮虧(MAE)。"""
        is_long = position.direction is Direction.LONG
        best, worst = (high, low) if is_long else (low, high)

        for price, attr in ((best, "mfe_pct"), (worst, "mae_pct")):
            change = (
                (price - position.entry_price) / position.entry_price
                if is_long
                else (position.entry_price - price) / position.entry_price
            )
            pct = change * position.leverage * 100
            if attr == "mfe_pct":
                position.mfe_pct = max(position.mfe_pct, pct)
            else:
                position.mae_pct = min(position.mae_pct, pct)

    def _close(self, position, raw_price, candle, index, reason, liquidated=False):
        is_long = position.direction is Direction.LONG

        # 強平沒有滑點優惠,但停損停利是市價單,一樣有滑點
        exit_price = self.costs.exit_price(raw_price, is_long)

        change = (
            (exit_price - position.entry_price) / position.entry_price
            if is_long
            else (position.entry_price - exit_price) / position.entry_price
        )
        gross = position.notional * change

        hours = (index - position.entry_index) * self.candle_hours
        funding = self.costs.funding_cost(position.notional, hours, is_long)

        exit_fee = self.costs.fee(position.notional)
        if liquidated:
            exit_fee += self.costs.liquidation_cost(position.notional)

        position.exit_index = index
        position.exit_time = candle["time"]
        position.exit_price = exit_price
        position.exit_reason = reason
        position.gross_pnl = gross
        position.fees += exit_fee
        position.funding = funding
        position.net_pnl = gross - position.fees - funding

        # 虧損不可能超過保證金 —— 強平就是為了這個
        if position.net_pnl < -position.size_usdt:
            position.net_pnl = -position.size_usdt

        return True

    def _force_close(self, position, candle, index, reason):
        return self._close(position, candle["close"], candle, index, reason)


def _normalise(candle):
    """接受 dict 或 [time, o, h, l, c, v] 兩種格式。"""
    if isinstance(candle, dict):
        return {
            "time": candle.get("time") or candle.get("timestamp") or 0,
            "open": float(candle["open"]),
            "high": float(candle["high"]),
            "low": float(candle["low"]),
            "close": float(candle["close"]),
            "volume": float(candle.get("volume") or 0),
        }
    return {
        "time": candle[0], "open": float(candle[1]), "high": float(candle[2]),
        "low": float(candle[3]), "close": float(candle[4]),
        "volume": float(candle[5]) if len(candle) > 5 else 0.0,
    }

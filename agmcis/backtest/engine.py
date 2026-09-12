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

    # ---- 第三十三節:部分成交與分批平倉 ----
    #
    # requested_quantity 是「想要的量」,quantity 是「實際拿到的量」。
    # 兩者相同時就是全額成交。分開記是因為一個在回測裡全額成交、
    # 在實盤只成交七成的策略,績效差距不是四捨五入等級的。
    requested_quantity: Optional[float] = None
    fill_ratio: float = 1.0

    # 分批平倉:已經收掉的比例與已實現的損益。
    # 剩餘比例 = 1 - closed_fraction。
    closed_fraction: float = 0.0
    realized_partial: float = 0.0
    partials: List[dict] = field(default_factory=list)
    tp_stage: int = 0

    # 停損被移動過幾次。移動停損有沒有真的在動,看這個數字最快。
    stop_moves: int = 0
    original_stop_loss: Optional[float] = None

    @property
    def notional(self):
        """**剩餘**部位的名目價值。分批收掉的部分不再計入。"""
        return self.size_usdt * self.leverage * self.remaining_fraction

    @property
    def full_notional(self):
        """建倉當下的名目價值。R 倍數與部分成交比例要用它。"""
        return self.size_usdt * self.leverage

    @property
    def remaining_fraction(self):
        return max(0.0, 1.0 - self.closed_fraction)

    @property
    def is_partially_filled(self):
        if self.requested_quantity is None or self.requested_quantity <= 0:
            return False
        return self.quantity < self.requested_quantity * (1 - 1e-9)

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
        """
        賺賠倍數:實際損益 ÷ 原本打算冒的風險。

        風險用**原始**停損與**建倉當下**的名目算 —— 停損被移動過之後,
        用現在的停損算會讓每一筆移動過停損的交易 R 倍數暴增,
        而那是計算方式造成的,不是策略變好了。
        """
        stop = self.original_stop_loss if self.original_stop_loss is not None else self.stop_loss
        risk = abs(self.entry_price - stop) / self.entry_price * self.full_notional
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
            "requested_quantity": (
                round(self.requested_quantity, 8)
                if self.requested_quantity is not None else None
            ),
            "fill_ratio": round(self.fill_ratio, 6),
            "partially_filled": self.is_partially_filled,
            "closed_fraction": round(self.closed_fraction, 6),
            "realized_partial": round(self.realized_partial, 4),
            "partials": list(self.partials),
            "stop_moves": self.stop_moves,
            "original_stop_loss": (
                round(self.original_stop_loss, 8)
                if self.original_stop_loss is not None else None
            ),
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
                 candle_hours=1.0, trailing=None, take_profit_stages=None,
                 fill_model=None):
        """
        trailing            移動停損設定(第五十八節)。None = 不移動。
                            {"mode": "percent", "gap_pct": 3.0}
                            {"mode": "atr", "multiple": 2.0, "atr_fn": fn}
                            atr_fn(history, index) -> ATR 值或 None。

        take_profit_stages  分批停利(第五十七節)。None = 一次全平。
                            [(R 倍數, 比例), ...],比例必須加總為 1。

        fill_model          部分成交(第三十三節)。None = 永遠全額成交。
                            fill_model(candle, notional) -> 0 到 1 的成交比例。

        三者預設全部關閉,而且關閉時的行為與加這些功能之前**完全相同** ——
        既有的回測結果不會因為引擎多了功能而改變。這很重要:
        一個會讓歷史結論悄悄改變的引擎升級,等於把過去的驗證全部作廢。
        """
        self.costs = costs if costs is not None else DEFAULT_COSTS
        self.start_balance = float(start_balance)
        self.risk_per_trade_pct = float(risk_per_trade_pct)
        self.max_leverage = float(max_leverage)
        self.maintenance_margin_ratio = float(maintenance_margin_ratio)
        self.candle_hours = float(candle_hours)
        self.trailing = trailing
        self.take_profit_stages = self._validate_stages(take_profit_stages)
        self.fill_model = fill_model

    @staticmethod
    def _validate_stages(stages):
        if not stages:
            return None

        total = sum(float(f) for _, f in stages)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"分批比例加起來是 {total},必須剛好是 1.0 —— "
                f"少於 1 會留下永遠不平的殘倉,多於 1 平不出來"
            )
        return [(float(r), float(f)) for r, f in stages]

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
            #
            # 順序:停損 / 強平 -> 分批停利 -> 移動停損。
            # 停損永遠先判,因為同根 K 棒無法知道誰先到,而假設停損先到
            # 是保守的那一邊(見模組說明)。分批停利也不例外。
            if position is not None:
                exited = self._check_exit(position, candle, index)
                if exited:
                    balance += self._settle(position)
                    result.trades.append(position)
                    position = None
                    pending_exit = False
                    equity_curve.append(round(balance, 8))

            # ---- 1a. 分批停利與移動停損 ----
            if position is not None:
                balance += self._check_partials(position, candle, index)
                self._trail_stop(position, candle, rows[: index + 1], index)

            # ---- 1b. 上一根策略喊的出場,在這一根開盤成交 ----
            #     停損停利優先於策略出場 —— 部位已經被停掉就沒得出場了。
            if position is not None and pending_exit:
                self._close(position, candle["open"], candle, index, "策略出場")
                balance += self._settle(position)
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
            balance += self._settle(position)
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

        requested_quantity = notional / entry_price
        quantity = requested_quantity
        fill_ratio = 1.0

        # ---- 部分成交(第三十三節)----
        # 實盤在流動性不足或價格快速移動時一定會遇到。回測假設永遠全額成交,
        # 等於把「這個策略的部位大小市場吃不吃得下」這個問題整個跳過。
        if self.fill_model is not None:
            fill_ratio = float(self.fill_model(candle, notional))
            if fill_ratio <= 0:
                return None
            fill_ratio = min(1.0, fill_ratio)
            quantity = requested_quantity * fill_ratio
            notional = notional * fill_ratio
            size_usdt = notional / leverage

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
            requested_quantity=requested_quantity,
            fill_ratio=fill_ratio,
            original_stop_loss=float(stop_loss),
        )
        position.fees = entry_fee
        return position

    # ---------------- 移動停損與分批停利 ----------------

    def _trail_stop(self, position, candle, history, index):
        """
        移動停損(第五十八節)。回傳有沒有移動。

        用 exit_plan.tighten_only 判斷候選值能不能採用,**不另外寫一套** ——
        「停損只能往有利方向移動」這條規則有一個實作就夠了,
        兩個實作遲早會有一個在某個邊界情況把停損往外推。
        """
        if not self.trailing:
            return False

        from agmcis.execution import exit_plan

        mode = self.trailing.get("mode", "percent")
        is_long = position.direction is Direction.LONG
        direction = "做多" if is_long else "做空"

        # 用**收盤價**當參考,不是這一根的極值。
        #
        # 跟著極值走比較像真實的移動停損,但它需要一個這個引擎在別的
        # 地方都拒絕的假設:「價格先到極值、才反轉」。如果那根 K 棒其實是
        # 先反轉再衝到極值,用極值算出來的停損在當時根本還不存在。
        #
        # 用收盤價就沒有這個問題:停損永遠落在已經確定發生的價格後面,
        # 而且它在**下一根**才生效(這一根的出場判定已經跑完了)。
        # 代價是抓到的利潤比真實的逐筆移動停損少一點 ——
        # 那是低估而不是高估,是可以接受的方向。
        reference = candle["close"]

        if mode == "percent":
            candidate = exit_plan.trail_percent(
                direction, reference, gap_pct=self.trailing.get("gap_pct", 3.0),
            )
        elif mode == "atr":
            atr_fn = self.trailing.get("atr_fn")
            atr = atr_fn(history, index) if atr_fn is not None else None
            candidate = exit_plan.trail_atr(
                direction, reference, atr,
                multiple=self.trailing.get("multiple", 2.0),
            )
        else:
            raise ValueError(f"未知的移動停損模式:{mode!r}")

        moved = exit_plan.tighten_only(direction, position.stop_loss, candidate)
        if moved is None:
            return False

        position.stop_loss = moved
        position.stop_moves += 1
        return True

    def _check_partials(self, position, candle, index):
        """
        分批停利(第五十七節)。回傳這一根實現了多少損益。

        與停損同根時**不收** —— 停損優先於停利是這個引擎一貫的
        保守假設(見模組說明),分批停利沒有理由例外。
        呼叫順序保證了這件事:_check_exit 先跑。
        """
        if not self.take_profit_stages:
            return 0.0

        is_long = position.direction is Direction.LONG
        stop = position.original_stop_loss or position.stop_loss
        risk = abs(position.entry_price - stop)
        if risk <= 0:
            return 0.0

        favourable = candle["high"] if is_long else candle["low"]
        realised = 0.0

        for stage, (r_multiple, fraction) in enumerate(
            self.take_profit_stages, start=1,
        ):
            if stage <= position.tp_stage:
                continue

            target = position.entry_price + (
                risk * r_multiple * (1 if is_long else -1)
            )

            reached = favourable >= target if is_long else favourable <= target
            if not reached:
                break       # 目標是依序的,第一個沒到就不用看後面

            # 最後一階由 _check_exit / 主迴圈的全平路徑處理,
            # 這裡只收中間的階 —— 否則會留下一個 0 比例的部位。
            if abs(position.closed_fraction + fraction - 1.0) < 1e-9:
                break

            realised += self._realise_partial(
                position, target, fraction, stage, index,
            )

        return realised

    def _realise_partial(self, position, price, fraction, stage, index):
        """收掉一部分部位。回傳這一腿的淨損益。"""
        is_long = position.direction is Direction.LONG
        exit_price = self.costs.exit_price(price, is_long)

        leg_notional = position.full_notional * fraction

        change = (
            (exit_price - position.entry_price) / position.entry_price
            if is_long
            else (position.entry_price - exit_price) / position.entry_price
        )
        gross = leg_notional * change

        hours = (index - position.entry_index) * self.candle_hours
        funding = self.costs.funding_cost(leg_notional, hours, is_long)
        fee = self.costs.fee(leg_notional)

        leg_pnl = gross - fee - funding

        position.closed_fraction = round(position.closed_fraction + fraction, 10)
        position.tp_stage = stage
        position.realized_partial += leg_pnl
        position.fees += fee
        position.funding += funding
        position.partials.append({
            "stage": stage,
            "index": index,
            "fraction": fraction,
            "price": round(exit_price, 8),
            "pnl": round(leg_pnl, 4),
        })

        return leg_pnl

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
        position.funding += funding

        # 這一腿的損益。分批已經實現的部分另外加 ——
        # 它已經被算進 balance 了,但 net_pnl 要代表**整筆交易**,
        # 否則一筆「TP1 收 +90、剩下虧 -20」會被統計成虧損交易。
        final_leg = gross - position.fees - position.funding

        # 虧損不可能超過保證金 —— 強平就是為了這個。
        # 上限用剩餘部位的保證金:已經收掉的部分不會再虧。
        remaining_margin = position.size_usdt * position.remaining_fraction
        if final_leg < -remaining_margin:
            final_leg = -remaining_margin

        position.net_pnl = final_leg + position.realized_partial

        # 主迴圈把 net_pnl 加進 balance,但分批那部分已經加過了。
        # 回傳的是「這一次要加進 balance 的金額」,由 _settle 取用。
        position._final_leg_pnl = final_leg
        return True

    @staticmethod
    def _settle(position):
        """
        平倉時要加進餘額的金額。

        **不是 net_pnl。** net_pnl 是整筆交易的損益(含分批),
        而分批那部分在發生的當下就已經加進餘額了。再加一次等於憑空生錢。
        """
        return getattr(position, "_final_leg_pnl", position.net_pnl)

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

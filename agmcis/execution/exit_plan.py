"""
出場計畫(Master Prompt 第五十五 ~ 五十八節)。

進場只有一個決定:要不要開。出場有一連串:先收多少、什麼時候把停損
拉到成本、剩下的跟多遠、跟到什麼時候放棄。這個模組把那一連串寫成
**純函式** —— 給它部位與價格,它回一個動作,不碰資料庫也不送單。

分開的理由不是好看。出場邏輯的錯誤幾乎都不會當場爆炸,它們會變成
一連串「怎麼這筆賺得比預期少」,而那種錯誤只有在可以單獨測試的時候
才抓得出來。

## 四條不讓步的規則

**一、停損只能往有利的方向移動。**
做多的停損只能往上,做空的只能往下。這條規則沒有例外,也沒有
「因為波動變大所以放寬一點」的情況 —— 那正好是最想放寬的時候,
也正好是最不該放寬的時候。

**二、分批停利不是「一筆交易結束」。**
拿了 TP1 不代表這筆賺錢。統計上把 TP1 算成一筆獲利交易,勝率會
衝到接近 100%:你永遠先收 TP1,而虧的那些還開著。整筆交易的
損益要等最後一部分平掉才算數,這件事在資料層與統計層都必須成立。

**三、移到成本價要留手續費的空間。**
「Break Even」如果真的放在進場價,兩趟手續費加滑點會讓它變成
一筆小虧。所以成本價是進場價往有利方向推一點點。

**四、時間出場不是虧損出場。**
一個放了三天沒有動的部位佔著保證金與注意力,但它不是錯的 ——
所以時間出場的預設是「到期就平」,不是「到期且虧損才平」。
後者會讓所有虧損部位一直留著,那是處置效應的程式碼版本。
"""
import logging
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger("agmcis.execution.exit_plan")


# 分批停利的預設配置:R 倍數與各收多少比例。
# 實際比例必須由回測驗證 —— 這裡的數字是起點,不是結論。
DEFAULT_TARGETS = (
    (1.0, 0.30),   # 1R 收 30%
    (2.0, 0.30),   # 2R 再收 30%
    (3.0, 0.40),   # 3R 收完
)

# 移到成本價時,往有利方向推的緩衝(佔停損距離的比例)。
# 這是為了蓋掉來回手續費與滑點 —— 真的放在進場價會變成小虧。
BREAK_EVEN_BUFFER_R = 0.1

# ATR 移動停損的倍數。
DEFAULT_ATR_MULTIPLE = 2.0

# 百分比移動停損的間距。
DEFAULT_TRAIL_PCT = 3.0

# 時間出場:超過這麼多小時就平掉。
DEFAULT_MAX_HOLD_HOURS = 72


# 動作種類
NONE = "NONE"
PARTIAL_TP = "PARTIAL_TP"
FULL_TP = "FULL_TP"
MOVE_STOP = "MOVE_STOP"
TIME_EXIT = "TIME_EXIT"


@dataclass
class ExitAction:
    kind: str = NONE
    fraction: Optional[float] = None
    new_stop: Optional[float] = None
    stage: Optional[int] = None
    reason: str = ""

    @property
    def is_noop(self):
        return self.kind == NONE

    def to_dict(self):
        return {
            "kind": self.kind,
            "fraction": self.fraction,
            "new_stop": self.new_stop,
            "stage": self.stage,
            "reason": self.reason,
        }


def _sign(direction):
    """做多 +1,做空 -1,其他 0。"""
    value = str(getattr(direction, "value", direction) or "").strip().upper()
    if value in ("做多", "LONG", "BUY", "看多"):
        return 1
    if value in ("做空", "SHORT", "SELL", "看空"):
        return -1
    return 0


@dataclass
class ExitPlan:
    """
    一個部位的完整出場計畫。

    所有價格都在建構時算好 —— 出場目標不該隨著行情重算。
    重算出場目標等於「賺了就把目標往上調」,那是把一個有紀律的計畫
    變成一個沒有紀律的希望。
    """
    direction: str
    entry: float
    stop_loss: float
    targets: List[dict] = field(default_factory=list)
    break_even: Optional[float] = None
    risk_distance: float = 0.0

    @property
    def sign(self):
        return _sign(self.direction)

    def to_dict(self):
        return {
            "direction": self.direction,
            "entry": self.entry,
            "stop_loss": self.stop_loss,
            "risk_distance": round(self.risk_distance, 8),
            "break_even": self.break_even,
            "targets": list(self.targets),
        }


def build(direction, entry, stop_loss, targets=DEFAULT_TARGETS,
          break_even_buffer_r=BREAK_EVEN_BUFFER_R, cap=None):
    """
    從進場價與停損算出分批停利的價格。

    用 **R 倍數**(停損距離的倍數)而不是固定百分比:停損放 1% 的部位
    和放 5% 的部位,同樣「賺 3%」的意義完全不同。R 倍數讓不同標的、
    不同波動度的部位可以用同一套規則。

    ## cap:為什麼最後一個目標要被交易本身的停利價夾住

    position_monitor 看的是 trades.takeprofit,碰到就**整筆**平掉。
    如果那個價格比這裡算出來的 TP3 近,分批計畫永遠走不到 TP2 ——
    第一次碰到停利價就被整個平掉了,而那不是任何人設計的行為,
    是兩套系統各做各的。

    所以把 cap 傳進來(呼叫端給的是交易的 takeprofit):
    到達或超過 cap 的那一階價格改成 cap,**剩下所有階的比例併進去**,
    後面的階直接丟掉。這樣兩邊在同一個價格結束,誰先跑到都一樣。
    """
    
    sign = _sign(direction)
    if sign == 0:
        raise ValueError(f"無法辨識的方向:{direction!r}")

    entry = float(entry)
    stop_loss = float(stop_loss)
    risk = abs(entry - stop_loss)

    if entry <= 0 or risk <= 0:
        raise ValueError(
            f"進場 {entry} 與停損 {stop_loss} 算不出風險距離"
        )

    plan = ExitPlan(
        direction=str(getattr(direction, "value", direction)),
        entry=entry, stop_loss=stop_loss, risk_distance=risk,
    )

    if abs(sum(float(f) for _, f in targets) - 1.0) > 1e-6:
        raise ValueError(
            f"分批比例加起來是 {sum(float(f) for _, f in targets)},必須剛好是 1.0 —— "
            f"少於 1 會留下永遠不平的殘倉,多於 1 平不出來"
        )

    cap_value = None
    if cap is not None:
        try:
            candidate = float(cap)
        except (TypeError, ValueError):
            candidate = None
        # 方向錯的停利價直接忽略。做多的停利在進場價下方是資料錯誤,
        # 拿來夾住目標會讓 TP1 變成一個立刻成立的虧損出場。
        if candidate is not None and sign * (candidate - entry) > 0:
            cap_value = candidate

    remaining = list(targets)
    total = 0.0

    for index, (r_multiple, fraction) in enumerate(remaining, start=1):
        price = round(entry + sign * risk * float(r_multiple), 8)
        fraction = float(fraction)

        capped = (
            cap_value is not None
            and sign * (price - cap_value) >= 0
        )

        if capped:
            # 這一階(含之後所有階)全部在這個價格結束。
            fraction = round(1.0 - total, 6)
            price = cap_value

        total = round(total + fraction, 6)
        plan.targets.append({
            "stage": index,
            "r_multiple": float(r_multiple),
            "fraction": fraction,
            "price": price,
            "cumulative_fraction": total,
        })

        if capped:
            break

    if abs(total - 1.0) > 1e-6:
        # cap 比最後一階還遠時,比例本來就會加到 1;走到這裡代表
        # 上面的夾住邏輯有洞,寧可拒絕也不要留一個平不完的部位。
        raise ValueError(
            f"夾住停利價之後比例加起來是 {total},不是 1.0"
        )

    plan.break_even = round(
        entry + sign * risk * float(break_even_buffer_r), 8,
    )
    return plan


# ---------------- 移動停損 ----------------

def trail_atr(direction, price, atr, multiple=DEFAULT_ATR_MULTIPLE):
    """
    ATR 移動停損。波動大的時候跟得遠一點,波動小的時候跟得近一點。

    算不出來回 None —— **不是回一個猜的數字**。一個猜出來的停損價
    比沒有停損更糟:它看起來像保護。
    """
    sign = _sign(direction)
    if sign == 0 or not atr or atr <= 0 or not price or price <= 0:
        return None
    return round(price - sign * float(atr) * float(multiple), 8)


def trail_percent(direction, price, gap_pct=DEFAULT_TRAIL_PCT):
    sign = _sign(direction)
    if sign == 0 or not price or price <= 0 or not gap_pct or gap_pct <= 0:
        return None
    return round(price * (1 - sign * float(gap_pct) / 100.0), 8)


def trail_structure(direction, candles, lookback=10, buffer_pct=0.1):
    """
    結構型移動停損:停損放在最近的擺動低點(做多)或高點(做空)之外。

    這是三種裡面唯一真的跟「市場結構」有關的 —— ATR 與百分比都只是
    距離現價多遠。結構型停損被打到,代表的是「趨勢結構破了」,
    而不是「跌了 3%」。

    candles 是 [{high, low}, ...],不足 lookback 根就回 None。
    """
    sign = _sign(direction)
    if sign == 0 or not candles or len(candles) < lookback:
        return None

    window = candles[-lookback:]

    try:
        if sign > 0:
            level = min(float(c["low"]) for c in window)
            return round(level * (1 - float(buffer_pct) / 100.0), 8)
        level = max(float(c["high"]) for c in window)
        return round(level * (1 + float(buffer_pct) / 100.0), 8)
    except (KeyError, TypeError, ValueError):
        logger.warning("結構型停損:K 線資料不完整,不給停損價")
        return None


def tighten_only(direction, current_stop, candidate):
    """
    只接受把停損拉得更緊的候選值。

    這是整個模組最重要的三行。每一個移動停損的實作都有機會在某個
    邊界情況把停損往外推 —— 而那一次就足以把一筆該小賺的交易
    變成一筆大虧。
    """
    if candidate is None:
        return None

    sign = _sign(direction)
    if sign == 0:
        return None

    if current_stop is None:
        return candidate

    current_stop = float(current_stop)

    if sign > 0:
        return candidate if candidate > current_stop else None
    return candidate if candidate < current_stop else None


# ---------------- 主判斷 ----------------

def _reached(sign, price, target):
    return price >= target if sign > 0 else price <= target


def decide(plan, position, price, high=None, low=None, atr=None,
           candles=None, held_hours=None, trail_mode="atr",
           max_hold_hours=DEFAULT_MAX_HOLD_HOURS, **trail_kwargs):
    """
    下一個動作。一次只回**一個**動作。

    順序是刻意的:分批停利 -> 移到成本 -> 移動停損 -> 時間出場。
    先收錢再談保護,因為收錢是不可逆的機會(價格會跑掉),
    而保護在下一輪還可以做。

    `high` / `low` 是這一輪內的極值。只看輪詢當下的價格會漏掉
    「碰到 TP1 又跌回來」—— 那一次 TP1 是真的碰到了。
    """
    sign = plan.sign
    if sign == 0:
        return ExitAction(reason="方向不明,不做任何出場動作")

    price = float(price)
    stage = int(position.get("tp_stage") or 0)
    current_stop = position.get("stoploss")

    # 用區間極值判斷是否碰到目標;沒給就退回單點價格。
    favourable = float(high if high is not None else price) if sign > 0 else \
                 float(low if low is not None else price)

    # ---- 1. 分批停利 ----
    for target in plan.targets:
        if target["stage"] <= stage:
            continue

        if _reached(sign, favourable, target["price"]):
            is_last = target["stage"] == len(plan.targets)
            what = "平掉剩餘部位" if is_last else f"收 {target['fraction']:.0%}"
            return ExitAction(
                kind=FULL_TP if is_last else PARTIAL_TP,
                fraction=target["fraction"],
                stage=target["stage"],
                reason=(
                    f"TP{target['stage']} @ {target['price']} "
                    f"({target['r_multiple']:g}R),{what}"
                ),
            )
        break   # 目標是依序的,第一個沒到就不用看後面

    # ---- 2. 拿過 TP1 之後把停損移到成本價 ----
    if stage >= 1:
        moved = tighten_only(plan.direction, current_stop, plan.break_even)
        if moved is not None:
            return ExitAction(
                kind=MOVE_STOP, new_stop=moved,
                reason=f"TP1 已達成,停損移到成本價 {moved}(含手續費緩衝)",
            )

    # ---- 3. 移動停損 ----
    candidate = None
    if trail_mode == "atr":
        candidate = trail_atr(plan.direction, price, atr, **trail_kwargs)
    elif trail_mode == "percent":
        candidate = trail_percent(plan.direction, price, **trail_kwargs)
    elif trail_mode == "structure":
        candidate = trail_structure(plan.direction, candles, **trail_kwargs)
    else:
        raise ValueError(f"未知的移動停損模式:{trail_mode!r}")

    # 移動停損永遠不該把停損拉到比成本價還差的位置(拿過 TP1 之後)
    if candidate is not None and stage >= 1:
        candidate = (
            max(candidate, plan.break_even) if sign > 0
            else min(candidate, plan.break_even)
        )

    moved = tighten_only(plan.direction, current_stop, candidate)
    if moved is not None:
        return ExitAction(
            kind=MOVE_STOP, new_stop=moved,
            reason=f"{trail_mode} 移動停損:{current_stop} → {moved}",
        )

    # ---- 4. 時間出場 ----
    if (max_hold_hours is not None and held_hours is not None
            and float(held_hours) >= float(max_hold_hours)):
        return ExitAction(
            kind=TIME_EXIT, fraction=1.0,
            reason=(
                f"持有 {float(held_hours):.0f} 小時超過上限 {max_hold_hours} 小時,"
                f"這個部位佔著保證金但沒有在動"
            ),
        )

    return ExitAction(reason="沒有需要做的出場動作")


# ---------------- 套用到真實部位 ----------------
#
# 上面全部是純函式。這一段是唯一會產生副作用的地方,而且它只做
# 三件事:分批平倉、移動停損、時間出場。它**不會**開倉、不會加碼,
# 也不會把停損往外移(tighten_only 擋住了)。

def plan_for(position):
    """
    從一筆持倉建出出場計畫。建不出來回 None。

    停損用的是**建倉當下**的停損,不是現在的停損 —— 現在的停損
    可能已經被移動過了,用它算 R 倍數會讓目標一路往上飄。
    原始停損取不到時退回現在的停損,並在 log 說明。
    """
    direction = position.get("signal") or position.get("direction")
    entry = position.get("entry_price") or position.get("entry")
    stop = position.get("original_stoploss") or position.get("stoploss")

    if not direction or not entry or stop is None:
        return None

    try:
        # takeprofit 當上限:position_monitor 碰到它會整筆平掉,
        # 所以分批計畫的最後一階不能排在它後面。
        return build(direction, entry, stop, cap=position.get("takeprofit"))
    except ValueError as exc:
        logger.warning(
            "出場計畫建不起來 | %s | %s", position.get("symbol"), exc,
        )
        return None


def apply_to(position, price, high=None, low=None, atr=None, candles=None,
             held_hours=None, trade=None, monitor=None, **kwargs):
    """
    對一筆持倉執行一個出場動作。回傳 (動作, 結果 dict)。

    `trade` 是分批平倉的實作(預設 paper_trading),`monitor` 是
    移動停損的實作(預設 database_service.update_trade_stoploss)。
    兩個都可以注入,讓這一層可以在沒有資料庫的情況下測試。
    """
    symbol = position.get("symbol")
    plan = plan_for(position)

    if plan is None:
        return ExitAction(reason="建不出出場計畫"), None

    action = decide(
        plan, position, price, high=high, low=low, atr=atr,
        candles=candles, held_hours=held_hours, **kwargs
    )

    if action.is_noop:
        return action, None

    if action.kind == PARTIAL_TP:
        if trade is None:
            import paper_trading as trade
        result = trade.reduce_paper_trade(
            symbol, action.fraction, price, action.stage, reason=action.reason,
        )
        return action, result

    if action.kind in (FULL_TP, TIME_EXIT):
        if trade is None:
            import paper_trading as trade
        result = trade.close_paper_trade(symbol, price, action.reason)
        return action, result

    if action.kind == MOVE_STOP:
        if monitor is None:
            from database_service import update_trade_stoploss as monitor
        ok = monitor(symbol, action.new_stop)
        logger.info(
            "移動停損 | %s | %s → %s | %s",
            symbol, position.get("stoploss"), action.new_stop, action.reason,
        )
        return action, {"success": bool(ok), "new_stop": action.new_stop}

    return action, None


def run_exit_plans(positions=None, price_of=None, atr_of=None,
                   trade=None, monitor=None, **kwargs):
    """
    排程入口。對每一筆持倉跑一次出場計畫。

    單一標的失敗不會中斷整輪 —— 但一定會記錄。一個在第三個標的
    掛掉就不再處理後面的出場管理器,會讓後面的部位完全沒有人管。
    """
    if positions is None:
        from database_service import get_open_trades
        positions = get_open_trades()

    if price_of is None:
        from market_data import get_price as price_of

    actions = []
    errors = []

    for position in positions or []:
        symbol = position.get("symbol")
        try:
            price = price_of(symbol)
            if not price:
                errors.append(f"{symbol}: 取不到現價")
                continue

            atr = atr_of(symbol) if atr_of is not None else None
            action, result = apply_to(
                position, float(price), atr=atr,
                trade=trade, monitor=monitor, **kwargs
            )

            if not action.is_noop:
                actions.append({
                    "symbol": symbol,
                    "action": action.to_dict(),
                    "ok": bool((result or {}).get("success", True)),
                })
        except Exception as exc:
            logger.exception("出場計畫失敗 | %s", symbol)
            errors.append(f"{symbol}: {type(exc).__name__}: {exc}")

    return {
        "checked": len(positions or []),
        "actions": actions,
        "errors": errors,
    }

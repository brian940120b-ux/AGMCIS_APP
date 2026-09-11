"""
合約規則註冊表。

為什麼要單獨一層:
  每次下單都去打一次 load_markets 太慢,但規則又不能寫死 ——
  Master Prompt 第 11 條明確要求「BingX Trading Rules 必須動態取得」。
  這裡負責快取,並提供「把一個數量 / 價格調整成合約允許的值」的工具。

這一層只做「符不符合合約規則」的判斷,不碰風控。
風控決定「要不要開、開多大」,這裡決定「這個數字交易所收不收」。

(exchange, market_type, symbol) 是唯一鍵 ——
Standard 與 Perpetual 的同一個 symbol 規則不同,不能共用一份快取。
"""
import logging
import math
import time
from decimal import ROUND_DOWN, ROUND_UP, Decimal

from agmcis.core.enums import Direction, MarketType
from agmcis.core.errors import TradingRuleViolation

logger = logging.getLogger("agmcis.trading_rules")

DEFAULT_TTL_SECONDS = 3600


class TradingRulesRegistry:
    def __init__(self, adapter=None, ttl_seconds=DEFAULT_TTL_SECONDS, clock=time.monotonic):
        self._adapter = adapter
        self._ttl = ttl_seconds
        self._clock = clock
        self._cache = {}   # key -> (fetched_at, TradingRules)

    @property
    def adapter(self):
        if self._adapter is None:
            from agmcis.data.market_data import get_adapter
            self._adapter = get_adapter()
        return self._adapter

    def get(self, symbol, market_type=MarketType.PERPETUAL, refresh=False):
        market_type = MarketType.parse(market_type, MarketType.PERPETUAL)
        key = (self.adapter.name, market_type.value, symbol)
        now = self._clock()

        if not refresh and key in self._cache:
            fetched_at, rules = self._cache[key]
            if now - fetched_at < self._ttl:
                return rules

        rules = self.adapter.get_trading_rules(symbol, market_type)
        self._cache[key] = (now, rules)
        return rules

    def invalidate(self, symbol=None, market_type=None):
        if symbol is None:
            self._cache.clear()
            return
        market_type = MarketType.parse(market_type, MarketType.PERPETUAL)
        self._cache.pop((self.adapter.name, market_type.value, symbol), None)

    def cached_count(self):
        return len(self._cache)


# ---------------- 數值調整 ----------------

def _step_of(value):
    """
    ccxt 的 precision 可能是「小數位數」(3)也可能是「最小單位」(0.001)。
    統一轉成最小單位。
    """
    if value is None:
        return None
    value = float(value)
    if value <= 0:
        return None
    if value >= 1 and float(value).is_integer():
        return 10 ** (-int(value))
    return value


def round_to_step(value, step, mode=ROUND_DOWN):
    """
    把數值調整成 step 的整數倍。

    預設 ROUND_DOWN —— 數量寧可少一點也不要超出額度。
    價格則依方向決定(見 round_price)。
    """
    step = _step_of(step)
    if step is None or value is None:
        return value

    quantised = (Decimal(str(value)) / Decimal(str(step))).quantize(
        Decimal("1"), rounding=mode
    ) * Decimal(str(step))

    # 去掉浮點誤差造成的長尾
    decimals = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return float(round(float(quantised), decimals + 2))


def round_quantity(quantity, rules):
    """數量一律無條件捨去到 step_size —— 超出的部分交易所會直接拒單。"""
    return round_to_step(quantity, rules.step_size, ROUND_DOWN)


def round_price(price, rules, direction=None, is_stop=False):
    """
    價格調整到 tick_size。

    方向會影響進位方式,因為「安全」的方向不一樣:
      做多的停損往下取(更早離場比更晚好),做空的停損往上取。
    不指定方向時一律往下取。
    """
    if direction is None:
        return round_to_step(price, rules.tick_size, ROUND_DOWN)

    direction = Direction.parse(direction)
    if direction is None:
        return round_to_step(price, rules.tick_size, ROUND_DOWN)

    if is_stop:
        mode = ROUND_DOWN if direction is Direction.LONG else ROUND_UP
    else:
        mode = ROUND_UP if direction is Direction.LONG else ROUND_DOWN

    return round_to_step(price, rules.tick_size, mode)


# ---------------- 驗證 ----------------

def validate_quantity(quantity, price, rules):
    """
    檢查數量是否符合合約規則。

    回傳 (ok, problems)。problems 是清單,一次回報所有問題而不是只講第一個 ——
    下單被拒之後一次修好比來回試三次好。
    """
    problems = []

    if quantity is None or quantity <= 0:
        return False, [f"數量必須大於 0,收到 {quantity}"]

    if rules.min_qty is not None and quantity < rules.min_qty:
        problems.append(f"數量 {quantity} 低於最小下單量 {rules.min_qty}")

    if rules.max_qty is not None and quantity > rules.max_qty:
        problems.append(f"數量 {quantity} 超過最大下單量 {rules.max_qty}")

    step = _step_of(rules.step_size)
    if step:
        remainder = abs(quantity / step - round(quantity / step))
        if remainder > 1e-9:
            problems.append(f"數量 {quantity} 不是 step_size {step} 的整數倍")

    if rules.min_notional is not None and price:
        notional = quantity * float(price)
        if notional < rules.min_notional:
            problems.append(
                f"名目價值 {round(notional, 4)} 低於最小值 {rules.min_notional}"
            )

    return not problems, problems


def validate_price(price, rules):
    problems = []

    if price is None or price <= 0:
        return False, [f"價格必須大於 0,收到 {price}"]

    tick = _step_of(rules.tick_size)
    if tick:
        remainder = abs(price / tick - round(price / tick))
        if remainder > 1e-9:
            problems.append(f"價格 {price} 不是 tick_size {tick} 的整數倍")

    return not problems, problems


def validate_leverage(leverage, rules):
    problems = []

    if leverage is None or leverage <= 0:
        return False, [f"槓桿必須大於 0,收到 {leverage}"]

    if rules.max_leverage is not None and leverage > rules.max_leverage:
        problems.append(
            f"槓桿 {leverage}x 超過該合約上限 {rules.max_leverage}x"
        )

    return not problems, problems


def assert_valid_order(quantity, price, rules, leverage=None):
    """
    下單前的完整檢查。不符合就拋 TradingRuleViolation。

    ⚠️ 這只檢查「交易所收不收」。要不要開、開多大是 Risk Engine 的職責。
    """
    all_problems = []

    ok, problems = validate_price(price, rules)
    all_problems.extend(problems)

    ok, problems = validate_quantity(quantity, price, rules)
    all_problems.extend(problems)

    if leverage is not None:
        ok, problems = validate_leverage(leverage, rules)
        all_problems.extend(problems)

    if all_problems:
        raise TradingRuleViolation(
            f"{rules.symbol} ({rules.market_type.value})",
            "; ".join(all_problems),
        )

    return True


def quantity_for_notional(notional_usdt, price, rules):
    """
    把「我想開多少 USDT 的名目價值」換算成合約數量,並調整到合約允許的單位。

    回傳 (quantity, problems)。quantity 為 None 代表這個名目價值
    在這個合約上根本下不了單(通常是低於最小名目)。
    """
    if not price or price <= 0:
        return None, [f"價格無效: {price}"]

    contract_size = rules.contract_size or 1
    raw_quantity = float(notional_usdt) / (float(price) * float(contract_size))
    quantity = round_quantity(raw_quantity, rules)

    if not quantity or quantity <= 0:
        return None, [
            f"名目 {notional_usdt} USDT 在 {rules.symbol} 換算後不足最小下單單位"
        ]

    ok, problems = validate_quantity(quantity, price, rules)
    if not ok:
        return None, problems

    return quantity, []


_registry = None


def get_registry():
    global _registry
    if _registry is None:
        _registry = TradingRulesRegistry()
    return _registry


def set_registry(registry):
    global _registry
    _registry = registry

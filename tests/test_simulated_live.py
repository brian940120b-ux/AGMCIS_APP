"""
SIMULATED LIVE:實單路徑整條跑一次(不動真錢)。

## 這一組真正在守的東西

不是「情境會不會過」—— 那是 scripts/simulate_live.py 的事,這裡只是
把它掛進測試,免得它壞了沒人知道。

真正的重點是**模擬器的保真度**。這個專案在實單路徑上找到的每一個
bug 都是同一種:假物件跟真的不一樣,而測試照著假物件寫,所以是綠的。

    * position["hedged"] —— ccxt 寫死 None
    * 停損單的 type —— ccxt 把 stop_market 映射成 market
    * PARTIALLY_FILLED —— ccxt 映射成 open

一個「乾淨漂亮」的模擬器會把這些坑填平,然後測試又會全綠,而實盤
還是會炸。所以下面有一整組測試在確認模擬器**保留了那些坑**。
"""
import importlib.util
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.core.enums import OrderSide, OrderType
from agmcis.exchange.simulated import (
    CCXT_CLOSED,
    CCXT_OPEN,
    Failure,
    SimulatedExchange,
)

SYMBOL = "BTC-USDT"


def load_script():
    spec = importlib.util.spec_from_file_location(
        "simulate_live_script", "scripts/simulate_live.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestTheWholeLivePathRuns(unittest.TestCase):

    def test_every_scenario_falls_the_safe_way(self):
        script = load_script()
        lines = []

        ok, tally = script.run(writer=lines.append)

        self.assertTrue(ok, "\n".join(lines))

    def test_the_scenarios_actually_cover_the_dangerous_branches(self):
        """
        一個只跑正常路徑的模擬,證明不了任何實盤會遇到的事。
        """
        script = load_script()
        titles = [title for title, _ in script.SCENARIOS]

        for needed in ("停損被拒", "部分成交", "連線中斷", "平不掉"):
            with self.subTest(needed=needed):
                self.assertTrue(
                    any(needed in title for title in titles),
                    f"沒有涵蓋「{needed}」",
                )


class TestTheSimulatorKeepsCcxtsPotholes(unittest.TestCase):
    """
    模擬器必須**保留** ccxt 的坑,不可以把它們填平。

    填平了,測試會全綠而實盤照炸 —— 那比沒有模擬器更糟,
    因為它會給人一種驗過了的感覺。
    """

    def entry(self, exchange, quantity=0.01):
        return exchange.create_order(
            symbol=SYMBOL, side=OrderSide.BUY, quantity=quantity,
            order_type=OrderType.MARKET, client_order_id="cid-1",
        )

    def test_a_stop_order_reports_type_market(self):
        """
        ccxt 的 parse_order_type() 把 stop_market 映射成 market。
        停損單回來之後 type 裡的 "stop" 已經不見了。
        """
        exchange = SimulatedExchange()
        self.entry(exchange)
        exchange.create_order(
            symbol=SYMBOL, side=OrderSide.SELL, quantity=0.01,
            order_type=OrderType.STOP_MARKET, reduce_only=True,
            params={"stopPrice": 49000.0},
        )

        stop = exchange.open_stop_orders(SYMBOL)[0]

        self.assertEqual(stop["type"], "market")
        self.assertNotIn("stop", stop["type"])

    def test_a_stop_order_carries_the_trigger_price(self):
        """type 洗掉之後,觸發價是唯一可靠的訊號。"""
        exchange = SimulatedExchange()
        self.entry(exchange)
        exchange.create_order(
            symbol=SYMBOL, side=OrderSide.SELL, quantity=0.01,
            order_type=OrderType.STOP_MARKET, reduce_only=True,
            params={"stopPrice": 49000.0},
        )

        stop = exchange.open_stop_orders(SYMBOL)[0]

        self.assertEqual(stop["stopLossPrice"], 49000.0)

    def test_a_position_reports_hedged_as_none_even_in_hedge_mode(self):
        """
        ccxt 的 bingx parse_position 把 hedged 寫死成 None,不管帳戶
        是不是雙向持倉。讀那個欄位的程式碼在這裡一定要壞。
        """
        exchange = SimulatedExchange(hedged=True)
        self.entry(exchange)

        position = exchange.get_positions()[0]

        self.assertIsNone(position["hedged"])
        self.assertTrue(exchange.get_position_mode()["hedged"])

    def test_a_partial_fill_reports_status_open(self):
        """
        ccxt 把 PARTIALLY_FILLED 映射成 open —— 部分成交與完全沒成交
        在那個欄位上長得一模一樣。
        """
        exchange = SimulatedExchange(failures={Failure.PARTIAL_FILL})
        order = self.entry(exchange)

        self.assertEqual(order["status"], CCXT_OPEN)
        self.assertGreater(order["filled"], 0)
        self.assertLess(order["filled"], order["amount"])

    def test_the_raw_client_order_id_uses_bingx_spelling(self):
        """BingX 永續的原始欄位是 clientOrderID(大寫 ID)。"""
        exchange = SimulatedExchange()
        order = self.entry(exchange)

        self.assertEqual(order["clientOrderId"], "cid-1")
        self.assertEqual(order["info"]["clientOrderID"], "cid-1")

    def test_it_only_produces_statuses_ccxt_produces(self):
        """
        產生一個 ccxt 不會產生的狀態,等於又在驗一個想像中的世界。
        """
        exchange = SimulatedExchange()
        self.entry(exchange)
        exchange.create_order(
            symbol=SYMBOL, side=OrderSide.SELL, quantity=0.01,
            order_type=OrderType.STOP_MARKET, reduce_only=True,
            params={"stopPrice": 49000.0},
        )
        stop = exchange.open_stop_orders(SYMBOL)[0]
        exchange.cancel_order(stop["id"], SYMBOL)

        allowed = {CCXT_OPEN, CCXT_CLOSED, "canceled"}
        seen = {
            order["status"]
            for order in exchange.get_order_history(SYMBOL)
        }

        self.assertTrue(seen <= allowed, f"多出了 {seen - allowed}")


class TestTheSimulatorNeverTouchesTheNetwork(unittest.TestCase):

    def test_it_imports_nothing_that_can_reach_an_exchange(self):
        import ast
        import inspect

        from agmcis.exchange import simulated

        tree = ast.parse(inspect.getsource(simulated))
        imported = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])

        for forbidden in ("ccxt", "requests", "httpx", "urllib", "socket"):
            with self.subTest(module=forbidden):
                self.assertNotIn(forbidden, imported)

    def test_it_needs_no_credentials(self):
        """
        金鑰只在 VPS 的 .env 裡。模擬器要在任何機器上都跑得起來,
        否則它就不是一個可以隨時跑的驗證。
        """
        exchange = SimulatedExchange()

        order = exchange.create_order(
            symbol=SYMBOL, side=OrderSide.BUY, quantity=0.01,
            order_type=OrderType.MARKET,
        )

        self.assertTrue(order["id"])


class TestItIsNotAClaimAboutLiveTrading(unittest.TestCase):
    """
    第二節與第九十九節:不得在沒驗證的情況下宣稱結果。
    """

    def test_the_script_says_it_does_not_authorise_live_trading(self):
        source = open("scripts/simulate_live.py", encoding="utf-8").read()

        self.assertIn("不代表可以下實單", source)
        self.assertIn("LIVE SAFETY GATE", source)

    def test_the_simulator_does_not_live_in_the_execution_package(self):
        """
        LIVE SAFETY GATE 掃 agmcis/execution 找實單程式碼。
        模擬器放進去會讓閘門把它當成實單路徑,那是錯的訊號。
        """
        from agmcis.exchange import simulated

        self.assertNotIn("execution", simulated.__name__)

    def test_the_gate_still_only_sees_the_real_live_broker(self):
        from agmcis.safety.live_gate import LiveGate

        found = LiveGate()._scan_live_broker_sources()
        files = {where for where, _name, _why in found}

        self.assertNotIn("agmcis/exchange/simulated.py", files)


if __name__ == "__main__":
    unittest.main()

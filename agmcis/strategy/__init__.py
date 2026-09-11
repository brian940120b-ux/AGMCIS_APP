"""
策略層。

鐵律:策略**不得** import 任何 exchange 模組。
它只看指標與市況,產出一個看法(StrategyVerdict),
要不要真的開、開多大是 Risk Engine 的事。
"""

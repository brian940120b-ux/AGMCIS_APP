"""
交易所存取層。

鐵律:策略層、訊號層與 Agent 層**不得**直接呼叫交易所。
它們只能透過 ExchangeAdapter 介面,而且只能讀行情 ——
下單一律走 Execution Engine(Phase 12)。
"""

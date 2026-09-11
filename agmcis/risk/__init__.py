"""
Risk Engine。

鐵律:任何開倉路徑都必須先通過這一層,而且它可以否決任何 Agent 的建議。
即使所有策略都說 STRONG BUY,Risk Engine 說 REJECT 就是不開。
"""

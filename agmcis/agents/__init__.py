"""
Multi-Agent 層。

架構鐵律(Master Prompt 第三十二節):

    **Agent 不得直接呼叫下單 API。**

Agent 只能看資料、只能產生意見。意見彙總成 TradeIntent,
TradeIntent 必須通過 Risk Engine 這道 hard gate,才輪得到 Execution Engine。

    Agents -> AgentOpinion -> Consensus -> TradeIntent
           -> Risk Engine (hard gate) -> Execution Engine -> Exchange

這不是靠自律維持的。`agmcis/agents/` 底下的模組**不得 import**
exchange、execution 或 database 層,有測試用 AST 掃描把這件事釘死。

為什麼要這樣切:LLM 與多因子評分都會出錯,而且會用很有說服力的語氣出錯。
把下單能力放在它們構不到的地方,錯誤就只會變成一個被拒絕的 intent,
而不是一張真的送出去的單。
"""

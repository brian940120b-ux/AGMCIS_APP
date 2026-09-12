"""
向下相容 shim。

`analyze_symbol()` 曾經是**第二套評分公式**:EMA 15+20、RSI 15、
MACD 20、量 15、ADX 15,從 0 累加到 100,>= 75 做多、<= 25 做空。

它與 `technical_service` 那一套同時存在,而兩套用不同的均線
(EMA50 vs EMA60)、不同的權重、不同的門檻 —— 同一根 K 棒可以在
一邊得 80 分、在另一邊得 55 分。Phase 6 把兩套合併成一條管線
(`agmcis/signal/pipeline.py`),評分改由 `agmcis/signal/scorer.py`
負責,指標由 `agmcis/analysis/indicators.py` 統一計算。

合併之後這個函式就沒有人呼叫了,但它留在原地 132 行 ——
`tests/test_strategy_pipeline.py` 有一條測試在確保沒有模組再 import 它。
一份「沒有人用但還能跑」的評分公式是最危險的那種死碼:
它看起來是可以拿來用的,而它的答案跟活著的那條管線不一樣。

第九十五節列的 God Function,最好的處理方式不是把它拆成三個小函式,
是刪掉它 —— 拆開一段沒有人呼叫的程式碼,只會讓它看起來更值得保留。

現在的入口:

    from agmcis.signal import pipeline
    signal = pipeline.analyse_symbol("BTC/USDT")

注意拼法:新的是 `analyse_symbol`(英式),舊的是 `analyze_symbol`。
不同的名字是刻意的 —— 兩者的回傳型別不同(Signal 物件 vs dict),
用同一個名字會讓呼叫端以為可以直接替換。
"""


def analyze_symbol(*args, **kwargs):
    """
    已移除。舊的第二套評分公式,見模組說明。

    刻意**拋例外而不是轉呼叫新管線**:兩者的回傳型別不同
    (dict vs Signal),而且舊的 key 名稱(entry / stoploss / takeprofit
    在觀望時是字串 "-")跟新的完全對不起來。安靜地轉過去,
    呼叫端會拿到一個結構不同的東西,然後在別的地方壞掉。
    """
    raise NotImplementedError(
        "strategy.analyze_symbol() 已在 Phase 6 移除 —— 它是與 "
        "technical_service 互相矛盾的第二套評分。\n"
        "請改用 agmcis.signal.pipeline.analyse_symbol(symbol),"
        "它回傳的是 Signal 物件不是 dict。"
    )


__all__ = ["analyze_symbol"]

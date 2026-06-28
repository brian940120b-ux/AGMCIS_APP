from timeframes import TIMEFRAMES
from technical_service import get_indicators

def analyze_timeframes(symbol):
    results = {}

    for tf in TIMEFRAMES:
        results[tf] = get_indicators(symbol, tf)

    return results

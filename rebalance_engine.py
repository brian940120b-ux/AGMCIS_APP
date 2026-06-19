from portfolio_manager import get_portfolio_summary
from smart_ranking import get_smart_ranking


TARGET_MAX_SINGLE_ASSET = 40
TARGET_MEDIUM_SINGLE_ASSET = 25
HIGH_EXPOSURE_LIMIT = 70
MEDIUM_EXPOSURE_LIMIT = 40


def get_rebalance_recommendation():
    portfolio = get_portfolio_summary()
    ranking = get_smart_ranking()

    allocation = portfolio["allocation"]
    exposure_ratio = portfolio["exposure_ratio"]

    recommendations = []

    top_symbols = [
        item["symbol"]
        for item in ranking[:3]
    ]

    if exposure_ratio >= HIGH_EXPOSURE_LIMIT:
        recommendations.append({
            "type": "風險控制",
            "action": "降低總曝險",
            "reason": f"目前曝險比例 {exposure_ratio}% 偏高，建議降低總倉位。"
        })

    elif exposure_ratio >= MEDIUM_EXPOSURE_LIMIT:
        recommendations.append({
            "type": "風險提醒",
            "action": "控制新增倉位",
            "reason": f"目前曝險比例 {exposure_ratio}% 屬於中等，建議不要過度加碼。"
        })

    else:
        recommendations.append({
            "type": "風險狀態",
            "action": "可保守觀察",
            "reason": f"目前曝險比例 {exposure_ratio}% 偏低，仍有操作空間。"
        })

    for item in allocation:
        symbol = item["symbol"]
        percent = item["percent"]

        if percent > TARGET_MAX_SINGLE_ASSET:
            recommendations.append({
                "type": "配置過重",
                "action": f"減少 {symbol}",
                "reason": f"{symbol} 目前占比 {percent}% ，高於建議上限 {TARGET_MAX_SINGLE_ASSET}%。"
            })

        elif percent > TARGET_MEDIUM_SINGLE_ASSET:
            recommendations.append({
                "type": "配置提醒",
                "action": f"觀察 {symbol}",
                "reason": f"{symbol} 目前占比 {percent}% ，建議避免繼續加碼。"
            })

    current_symbols = [
        item["symbol"]
        for item in allocation
    ]

    for item in ranking[:3]:
        symbol = item["symbol"]
        score = item["score"]
        signal = item["signal"]

        if symbol not in current_symbols and score >= 70:
            recommendations.append({
                "type": "潛在加碼",
                "action": f"觀察 {symbol}",
                "reason": f"{symbol} Smart Score {score}，目前未持倉，可列入觀察。訊號：{signal}"
            })

    if not recommendations:
        recommendations.append({
            "type": "狀態正常",
            "action": "維持目前配置",
            "reason": "目前投資組合沒有明顯過度集中或高曝險問題。"
        })

    return {
        "portfolio": portfolio,
        "top_symbols": top_symbols,
        "recommendations": recommendations
    }
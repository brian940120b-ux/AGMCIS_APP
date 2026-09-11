def rank_decisions(decisions):
    """confidence 為 None(資料異常)的項目排在最後,不與有效訊號混在一起比較。"""
    return sorted(
        decisions,
        key=lambda d: (d.get("confidence") is not None, d.get("confidence") or 0),
        reverse=True,
    )

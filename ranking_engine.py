def rank_decisions(decisions):
    return sorted(
        decisions,
        key=lambda d: d.get("confidence", 0),
        reverse=True
    )


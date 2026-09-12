"""
第三十節:12 個 Agent 角色都存在,而且**該是 gate 的沒有變成一票**。

對照表在 docs/AGENT_ROLES.md。這一組測試盯的是那份表沒有說謊。
"""
from pathlib import Path

import pytest

from agmcis.agents.registry import get_registry

DOC = Path("docs/AGENT_ROLES.md")

# 表上標成「不是投票者」的那幾個。它們必須是子系統,不是 Agent。
NOT_VOTERS = {
    "agmcis/lab": "Quant Research —— 離線研究",
    "agmcis/agents/consensus.py": "Strategy Agent —— 它是彙總者",
    "agmcis/risk/engine.py": "Risk Manager —— 它是 hard gate(第十九節)",
    "agmcis/execution/engine.py": "Execution —— 不做判斷(第三十二節)",
    "agmcis/risk/portfolio.py": "Portfolio Manager —— 風控的一部分",
    "agmcis/review/live_metrics.py": "Performance Analyst —— 事後分析",
    "agmcis/review/self_review.py": "Self Review —— 事後分析",
    "agmcis/agents/supervisor.py": "Supervisor —— 它審查投票者",
}


def test_every_module_the_mapping_names_exists():
    """
    對照表指到一個不存在的檔案,那一列就只是一句話。
    """
    for path, role in NOT_VOTERS.items():
        assert Path(path).exists(), f"{role} 指到的 {path} 不存在"


def test_the_gates_are_not_voting_agents():
    """
    把 Risk Engine 變成一票,就是把一個否決權降級成
    「十五分之一的意見」—— 而那正是它存在的理由被取消的那一刻。
    """
    names = set(get_registry().names)

    for forbidden in ("risk", "risk_manager", "execution", "supervisor",
                      "portfolio", "quant", "performance", "self_review"):
        assert forbidden not in names, (
            f"{forbidden} 不該是投票 Agent —— 見 docs/AGENT_ROLES.md"
        )


def test_the_voting_roles_are_all_present():
    names = set(get_registry().names)

    for required in ("regime", "trend", "momentum", "volatility",
                     "news", "sentiment", "macro", "order_book"):
        assert required in names, f"缺少 {required} Agent"


def test_sentiment_and_macro_never_vote_directional():
    """
    一個「只投 WAIT」的約定,如果只寫在 docstring 裡,
    下一次有人加功能的時候會消失。
    """
    import ast

    source = Path("agmcis/agents/flow.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name not in ("SentimentAgent", "MacroAgent"):
            continue

        for inner in ast.walk(node):
            if (isinstance(inner, ast.Attribute)
                    and isinstance(inner.value, ast.Name)
                    and inner.value.id == "Vote"):
                assert inner.attr not in ("LONG", "SHORT"), (
                    f"{node.name} 投了 {inner.attr} —— "
                    f"它只能投 WAIT 或棄權,見 docs/AGENT_ROLES.md"
                )


def test_the_supervisor_can_only_take_actionability_away():
    """
    一個會投票的監督者,在檢查自己的票。
    """
    source = Path("agmcis/agents/supervisor.py").read_text(encoding="utf-8")

    # 它可以把 intent 設成 None,但不能自己造一個。
    assert "intent = None" in source or "intent=None" in source
    assert "TradeIntent(" not in source, "Supervisor 不該自己產生交易意圖"


def test_order_book_and_order_flow_are_not_confused():
    """
    `order_book` 是 Agent,`order_flow` 是策略。兩者在不同的層,
    搞混會讓人以為訂單簿的意見被算了兩次。
    """
    from agmcis.strategy.registry import get_registry as strategies

    assert "order_book" in set(get_registry().names)
    assert "order_book" not in set(strategies().names)
    assert "order_flow" in set(strategies().names)
    assert "order_flow" not in set(get_registry().names)


def test_the_mapping_document_covers_all_twelve_roles():
    text = DOC.read_text(encoding="utf-8")

    for index in range(1, 13):
        assert f"| {index:02d} " in text, f"對照表少了 Agent {index:02d}"


def test_the_document_explains_why_the_gates_are_not_agents():
    """
    「它不是 Agent」是一個決定,不是一個遺漏。文件要說得出理由,
    否則下一個人會把它「補上」。
    """
    text = DOC.read_text(encoding="utf-8")

    assert "hard gate" in text
    assert "第十九節" in text
    assert "第三十二節" in text

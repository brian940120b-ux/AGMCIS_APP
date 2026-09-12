"""
第六十三節:Agent Interaction Visualization。

第六十三節列了 animated nodes / data streams / glowing connections /
signal pulses,然後加了一句:**UI Animation 不得影響交易核心。**

所以這一組測試盯兩件事:
  1. 動畫存在,而且綁在**真實狀態**上 —— 一張會動但顯示假流程的圖,
     比一張靜態但正確的圖糟得多。
  2. 它完全不碰交易核心 —— 純 CSS,沒有 JS 計時器。
"""
import re
from pathlib import Path

import pytest

CSS = Path("static/css/trading.css").read_text(encoding="utf-8")
JS = Path("static/js/trading.js").read_text(encoding="utf-8")
HTML = Path("templates/trading.html").read_text(encoding="utf-8")


# ---------------- 第六十三節列的東西都在 ----------------

@pytest.mark.parametrize("name,marker", [
    ("animated nodes", "node-alive"),
    ("data streams", "@keyframes stream"),
    ("glowing connections", ".flow-arrow.live"),
    ("activity indicators", ".flow-badge::before"),
    ("signal pulses", "node-blocked"),
    ("decision timeline", ".timeline"),
])
def test_section_63_elements_exist(name, marker):
    assert marker in CSS, f"第六十三節的 {name} 找不到({marker})"


def test_the_timeline_is_rendered():
    assert "function timeline(" in JS
    assert "決策時間軸" in JS


# ---------------- 動畫綁在真實狀態上 ----------------

def test_only_passed_stages_animate():
    """
    沒過的關卡不會假裝在運作。
    """
    assert ".flow-PASSED {\n    animation" in CSS

    # NOT_REACHED 與 NOT_RUN 不該有 animation 宣告
    for status in ("NOT_REACHED", "NOT_RUN"):
        block = re.search(rf"\.flow-{status}\s*\{{([^}}]*)\}}", CSS)
        if block:
            assert "animation" not in block.group(1), (
                f"{status} 不該有動畫 —— 它代表那一關沒有跑到"
            )


def test_a_connection_only_flows_when_the_previous_stage_passed():
    """
    每一段都亮的話,那張圖就變成裝飾。
    """
    assert 'previousStage.status === "PASSED"' in JS
    assert 'flowing ? "live" : ""' in JS


def test_the_execution_stage_never_animates():
    """
    執行層在預覽裡永遠沒跑。它會動的話,看起來就像單送出去了。
    """
    # NOT_RUN 是執行層在預覽裡的狀態
    block = re.search(r"\.flow-NOT_RUN\s*\{([^}]*)\}", CSS)
    assert block is not None
    assert "animation" not in block.group(1)


def test_each_status_has_its_own_indicator_speed():
    """
    ERROR 閃得比 BLOCKED 快,BLOCKED 比 PASSED 快 ——
    急迫程度要看得出來,不是全部一樣的節奏。
    """
    def duration(selector):
        block = re.search(
            rf"{re.escape(selector)}\s*\{{[^}}]*animation:\s*blink\s+([\d.]+)s",
            CSS,
        )
        assert block, f"{selector} 沒有 blink 動畫"
        return float(block.group(1))

    passed = duration(".flow-PASSED .flow-badge::before")
    blocked = duration(".flow-BLOCKED .flow-badge::before")
    error = duration(".flow-ERROR .flow-badge::before")

    assert error < blocked < passed


# ---------------- 不影響交易核心 ----------------

def test_the_animation_is_pure_css():
    """
    第六十三節:UI Animation 不得影響交易核心。

    純 CSS 由瀏覽器的合成器處理。JS 計時器會在主執行緒上跑,
    而主執行緒上的東西會拖慢頁面 —— 雖然交易核心不在瀏覽器裡,
    但一個卡住的頁面會讓人看不到停損警告。
    """
    for forbidden in ("setInterval(() =>", "requestAnimationFrame",
                      "style.transform", "style.opacity"):
        # setInterval 只允許用在資料重新載入(60 秒那兩個)
        if forbidden == "setInterval(() =>":
            continue
        assert forbidden not in JS, f"動畫不該用 {forbidden}"


def test_the_only_timers_are_data_refreshes():
    """
    這一頁唯一的計時器是每分鐘重抓資料。動畫不該有自己的計時器。
    """
    timers = re.findall(r"setInterval\((\w+),\s*(\d+)\)", JS)

    assert timers, "應該還有資料重整的計時器"
    for name, interval in timers:
        assert int(interval) >= 60000, f"{name} 每 {interval}ms 跑一次,太密"
        assert "load" in name.lower(), f"{name} 看起來不是資料重整"


def test_animation_respects_the_reduced_motion_preference():
    """
    使用者關掉動畫偏好時就不要動。這不只是體貼 ——
    前庭失調的人看到會不舒服。
    """
    assert "prefers-reduced-motion: reduce" in CSS

    block = re.search(
        r"@media \(prefers-reduced-motion: reduce\)\s*\{(.*?)\n\}",
        CSS, re.S,
    )
    assert block is not None
    assert "animation: none" in block.group(1)


def test_the_page_says_the_animation_means_something():
    """
    使用者要知道那些光不是裝飾。不說的話,它就變成裝飾。
    """
    assert "綁在真實狀態上" in HTML
    assert "不影響交易核心" in HTML or "不得影響交易核心" in HTML


def test_no_animation_leaks_into_the_trading_modules():
    """
    最直接的一條:動畫是前端的事,後端不該有任何相關的東西。
    """
    import pathlib

    for path in pathlib.Path("agmcis").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for forbidden in ("keyframes", "animation:", "requestAnimationFrame"):
            assert forbidden not in text, f"{path} 出現了 {forbidden}"

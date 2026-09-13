"""
研究腳本跑之前要成立的條件(Master Prompt 第二 / 十一 / 九十九節)。

## 為什麼需要這個模組

Strategy Lab 與出場調校都是回測。回測會扣手續費、算資金費率、
判斷有沒有被強平 —— 而那些數字全部來自合約規格快照。

**快照不存在的時候,系統用的是保守猜測值。** 那不是壞事:估計值
讓開發時跑得動。壞的是回測**照樣印出 PASS / REJECT**,而報告上
看不出來輸入是猜的。

第二節禁止「忽略手續費 / 滑點 / 資金費率」與「在沒驗證的情況下
宣稱結果」。一份用猜測費率算出來的 PASS,正好同時踩到兩條。

所以這裡做兩件事:

  1. **預設擋下來。** 沒校準就不要跑,先去跑
     `scripts/verify_bingx.py --write-specs`。
  2. 真的要在未校準的狀態下跑(例如手上根本連不到 BingX),
     用 `--allow-uncalibrated`。這時候**報告裡會蓋一個章** ——
     那份 JSON 自己會說「我的輸入是猜的」,而不是靠人記得。

第二點比第一點重要。擋得住的東西總有一天會被繞過;
蓋在報告上的章會跟著報告一起被讀到。
"""
import logging

logger = logging.getLogger("agmcis.lab.preconditions")

# 報告裡放這個章的欄位名。兩支腳本共用同一個鍵,
# 讀報告的人才不用記兩個名字。
STAMP_KEY = "input_quality"

OK = "CALIBRATED"
MISSING = "NOT_CALIBRATED"
STALE = "STALE"
TESTNET = "TESTNET"
UNKNOWN = "UNKNOWN"

_HEADLINES = {
    OK: "合約規格已校準",
    MISSING: "沒有合約規格快照 —— 手續費、維持保證金率、資金費率都是保守猜測值",
    STALE: "合約規格快照已過期 —— 費率與保證金分層會變",
    TESTNET: "合約規格快照來自 VST 測試環境 —— 測試網的費率不一定等於正式環境",
    UNKNOWN: "讀不到校準狀態 —— 讀不到不等於沒問題",
}


class InputQuality:
    """
    回測輸入的品質。**不是**「回測結果好不好」,是「算它的數字可不可信」。
    """

    def __init__(self, status, detail=None, captured_at=None, age_days=None):
        self.status = status
        self.detail = detail or ""
        self.captured_at = captured_at
        self.age_days = age_days

    @property
    def calibrated(self):
        return self.status == OK

    @property
    def headline(self):
        return _HEADLINES.get(self.status, _HEADLINES[UNKNOWN])

    def to_dict(self):
        """
        蓋在報告 JSON 上的章。

        `trustworthy` 這個鍵是給之後讀報告的人(或程式)看的 ——
        一份 trustworthy=false 的報告不可以拿去支持任何結論。
        """
        return {
            "status": self.status,
            "trustworthy": self.calibrated,
            "headline": self.headline,
            "detail": self.detail,
            "captured_at": self.captured_at,
            "age_days": self.age_days,
        }

    def lines(self):
        mark = "✓" if self.calibrated else "⚠️ "
        out = [f"  {mark} 回測輸入:{self.headline}"]
        if self.detail:
            out.append(f"       {self.detail}")
        return out


def check(symbols):
    """
    看合約規格校準到什麼程度。**讀不到算沒通過**,不是算通過。
    """
    try:
        from agmcis.exchange import specs
        report = specs.get_store().calibration_report(list(symbols))
    except Exception as exc:
        logger.error("讀不到校準狀態 | %s", exc)
        return InputQuality(UNKNOWN, f"{type(exc).__name__}: {exc}")

    if not report.get("calibrated"):
        return InputQuality(
            MISSING,
            "先跑 .venv/bin/python scripts/verify_bingx.py --write-specs",
        )

    captured_at = report.get("captured_at")
    age_days = report.get("age_days")

    if report.get("testnet"):
        return InputQuality(TESTNET, "", captured_at, age_days)

    if report.get("stale"):
        return InputQuality(
            STALE, f"快照已經 {age_days} 天沒更新", captured_at, age_days,
        )

    return InputQuality(OK, "", captured_at, age_days)


def require(symbols, allow_uncalibrated=False, writer=print):
    """
    回傳 InputQuality,並在該擋的時候回 None。

    None 代表**呼叫端應該結束,而且不要寫報告** ——
    一份用猜測費率算出來的報告,存在本身就是危險的:
    幾週之後沒有人記得它是在什麼條件下跑出來的。
    """
    quality = check(symbols)

    for line in quality.lines():
        writer(line)

    if quality.calibrated:
        return quality

    if not allow_uncalibrated:
        writer("")
        writer("  這一次不跑。回測會扣手續費、算資金費率、判斷強平,")
        writer("  而那些數字現在是猜的 —— 跑出來的 PASS 或 REJECT 都不算數")
        writer("  (第二節:不得忽略費用,也不得在沒驗證的情況下宣稱結果)。")
        writer("")
        writer("  校準之後再跑:")
        writer("      .venv/bin/python scripts/verify_bingx.py --write-specs")
        writer("")
        writer("  真的要在這個狀態下跑,加 --allow-uncalibrated ——")
        writer("  報告裡會蓋上 trustworthy=false,而那個章拿不掉。")
        return None

    writer("")
    writer("  ⚠️  --allow-uncalibrated:照跑,但報告會蓋上 trustworthy=false。")
    writer("      這份結果不可以拿去支持任何結論。")
    writer("")
    return quality

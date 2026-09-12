"""
LIVE SAFETY GATE(Master Prompt 第一百零二節)。

**這個閘門我打不開。**

Master Prompt 說得很清楚:Live Trading / API Key / Risk Limit 需要人確認。
所以這個模組的設計前提是:寫它的人(我)不應該有辦法讓它通過。

做法是把「人的確認」做成一個**只有人能產生**的東西:
一個檔案,內容必須由人手動寫入,包含一句必須逐字打出來的話,
而且有效期只有 24 小時。

自動化可以寫檔案 —— 這一點擋不住。但:

  * 確認檔**不進版控**(在 .gitignore 裡),所以我不會提交一份給你。
  * 它會過期,所以舊的確認不會一直生效。
  * 它必須指名批准的金額上限,所以「批准過一次」不等於「批准所有金額」。
  * 每一次閘門評估都寫稽核紀錄,包含通過與沒通過的項目。

最後,即使全部通過,**系統仍然無法下實單** —— `LiveBroker` 不存在。
這個閘門是實單的**前提**,不是開關。真正要接實單時,
那段程式碼本身還要再經過一次審視。
"""
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("agmcis.safety.live_gate")

CONFIRMATION_FILE = os.getenv("LIVE_GATE_CONFIRMATION", "live_gate_confirmation.json")
AUDIT_FILE = os.getenv("LIVE_GATE_AUDIT", "live_gate_audit.log")

# 確認的有效期。過期的確認不生效 —— 「上個月批准過」不等於「現在批准」。
CONFIRMATION_VALID_HOURS = 24

# 必須逐字打出來的句子。它很長是刻意的:
# 短句子容易被複製貼上,長句子會逼人讀過一次。
REQUIRED_PHRASE = (
    "我已經讀過 docs/PHASE_17_REPORT.md,"
    "我了解這個系統可能會虧光我投入的所有資金,"
    "我確認要用真實資金交易"
)

# 模擬盤最少要有這麼多筆已平倉交易才談得上實單
MIN_PAPER_TRADES = 100

# 模擬盤最少要跑這麼多天。筆數夠但只跑了兩天,代表沒有經歷過不同的市況。
MIN_PAPER_DAYS = 30

# 首次實單的單筆名目上限(USDT)。這個值刻意很小。
MAX_INITIAL_NOTIONAL_USDT = 50.0


@dataclass
class GateCheck:
    name: str
    passed: bool
    detail: str = ""
    blocking: bool = True

    def to_dict(self):
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "blocking": self.blocking,
        }


@dataclass
class GateResult:
    open: bool = False
    checks: List[GateCheck] = field(default_factory=list)
    evaluated_at: str = ""
    approved_notional: Optional[float] = None

    @property
    def failed(self):
        return [c for c in self.checks if not c.passed and c.blocking]

    @property
    def warnings(self):
        return [c for c in self.checks if not c.passed and not c.blocking]

    def to_dict(self):
        return {
            "open": self.open,
            "evaluated_at": self.evaluated_at,
            "approved_notional": self.approved_notional,
            "failed_count": len(self.failed),
            "checks": [c.to_dict() for c in self.checks],
        }

    def summary_lines(self):
        lines = [
            "LIVE SAFETY GATE:" + ("**開啟**" if self.open else "關閉"),
        ]
        for check in self.checks:
            mark = "✓" if check.passed else ("⛔" if check.blocking else "⚠️ ")
            lines.append(f"  {mark} {check.name}")
            if check.detail:
                lines.append(f"       {check.detail}")
        return lines


class LiveGate:
    """
    每個檢查都是獨立的函式,而且**失敗是預設結果** ——
    檢查本身出錯時回傳「沒通過」,不是「跳過」。

    一個在自己壞掉時預設放行的安全閘門不是安全閘門。
    """

    def __init__(self, confirmation_file=None, audit_file=None,
                 now=None, providers=None):
        self.confirmation_file = Path(confirmation_file or CONFIRMATION_FILE)
        self.audit_file = Path(audit_file or AUDIT_FILE)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._providers = providers or {}

    # ---------------- 各項檢查 ----------------

    def _provider(self, name, default):
        return self._providers.get(name, default)

    def check_confirmation(self):
        """
        人的確認。**這一項我打不開。**
        """
        if not self.confirmation_file.exists():
            return GateCheck(
                "人工確認", False,
                f"找不到確認檔 {self.confirmation_file}。\n"
                f"這個檔案必須由人手動建立,內容見 docs/PHASE_17_REPORT.md。",
            )

        try:
            data = json.loads(self.confirmation_file.read_text(encoding="utf-8"))
        except Exception as exc:
            return GateCheck("人工確認", False, f"確認檔無法解析:{exc}")

        phrase = str(data.get("phrase", "")).strip()
        if phrase != REQUIRED_PHRASE:
            return GateCheck(
                "人工確認", False,
                "確認句不符。必須逐字寫入 REQUIRED_PHRASE 的內容。",
            )

        signed_at = data.get("signed_at")
        try:
            signed = datetime.fromisoformat(str(signed_at))
            if signed.tzinfo is None:
                signed = signed.replace(tzinfo=timezone.utc)
        except Exception:
            return GateCheck("人工確認", False, f"signed_at 無法解析:{signed_at!r}")

        age = self._now() - signed
        if age > timedelta(hours=CONFIRMATION_VALID_HOURS):
            return GateCheck(
                "人工確認", False,
                f"確認已過期({age.total_seconds() / 3600:.1f} 小時前簽署,"
                f"有效期 {CONFIRMATION_VALID_HOURS} 小時)。\n"
                f"「上個月批准過」不等於「現在批准」。",
            )

        if age.total_seconds() < 0:
            return GateCheck("人工確認", False, "signed_at 在未來,拒絕接受。")

        notional = data.get("approved_notional_usdt")
        if notional is None:
            return GateCheck(
                "人工確認", False,
                "確認檔必須指名批准的單筆名目上限 approved_notional_usdt。\n"
                "「批准過一次」不等於「批准所有金額」。",
            )

        try:
            notional = float(notional)
        except (TypeError, ValueError):
            return GateCheck("人工確認", False,
                             f"approved_notional_usdt 不是數字:{notional!r}")

        if notional <= 0:
            return GateCheck("人工確認", False, "批准金額必須大於 0")

        if notional > MAX_INITIAL_NOTIONAL_USDT:
            return GateCheck(
                "人工確認", False,
                f"批准金額 {notional} USDT 超過首次實單上限 "
                f"{MAX_INITIAL_NOTIONAL_USDT} USDT。\n"
                f"第一次用真錢跑的規模應該小到虧光也不影響任何事。",
            )

        return GateCheck(
            "人工確認", True,
            f"已簽署({age.total_seconds() / 60:.0f} 分鐘前),"
            f"批准單筆名目上限 {notional} USDT",
        )

    def check_preflight(self):
        """上線前檢查不可以有 BLOCKER。"""
        runner = self._provider("preflight", None)
        if runner is None:
            return GateCheck(
                "上線前檢查", False,
                "沒有接上 preflight。請先執行 python scripts/preflight.py",
            )

        try:
            blockers = runner()
        except Exception as exc:
            return GateCheck("上線前檢查", False, f"執行失敗:{exc}")

        if blockers:
            return GateCheck(
                "上線前檢查", False,
                f"有 {len(blockers)} 項 BLOCKER:{', '.join(blockers)}",
            )

        return GateCheck("上線前檢查", True, "沒有 BLOCKER")

    def check_paper_record(self):
        """
        模擬盤的實績。筆數與天數都要夠 ——
        筆數夠但只跑了兩天,代表沒有經歷過不同的市況。
        """
        provider = self._provider("paper_record", None)
        if provider is None:
            return GateCheck("模擬盤實績", False, "取不到模擬盤紀錄")

        try:
            record = provider()
        except Exception as exc:
            return GateCheck("模擬盤實績", False, f"讀取失敗:{exc}")

        trades = int(record.get("closed_trades") or 0)
        days = float(record.get("days") or 0)

        problems = []
        if trades < MIN_PAPER_TRADES:
            problems.append(f"已平倉 {trades} 筆(需要 {MIN_PAPER_TRADES})")
        if days < MIN_PAPER_DAYS:
            problems.append(f"只跑了 {days:.1f} 天(需要 {MIN_PAPER_DAYS})")

        if problems:
            return GateCheck("模擬盤實績", False, ";".join(problems))

        return GateCheck("模擬盤實績", True, f"{trades} 筆 / {days:.0f} 天")

    def check_self_review(self):
        """
        自我檢討必須是 HEALTHY。

        FRAGILE 也不行 —— 績效依賴少數幾筆極端獲利的系統,
        用真錢跑只是把運氣放大。
        """
        provider = self._provider("self_review", None)
        if provider is None:
            return GateCheck("績效判定", False, "取不到自我檢討結果")

        try:
            review = provider()
        except Exception as exc:
            return GateCheck("績效判定", False, f"讀取失敗:{exc}")

        verdict = review.get("verdict")
        if verdict != "HEALTHY":
            return GateCheck(
                "績效判定", False,
                f"判定是 {verdict},必須是 HEALTHY。{review.get('headline', '')}",
            )

        return GateCheck("績效判定", True, review.get("headline", "HEALTHY"))

    def check_strategy_validation(self):
        """
        **live 實際在用的訊號管線**要通過 Phase 8 的樣本外驗證。

        只看「有沒有策略通過」是不夠的。系統裡還留著幾個舊的模組式策略,
        它們會一起被驗證,但 live 不會用它們 ——
        **驗證一組永遠不會下單的策略,等於沒有驗證。**

        所以這裡只認 is_live_pipeline=True 的結果。
        """
        provider = self._provider("strategy_validation", None)
        if provider is None:
            return GateCheck("策略驗證", False, "取不到策略驗證結果")

        try:
            result = provider()
        except Exception as exc:
            return GateCheck("策略驗證", False, f"讀取失敗:{exc}")

        rows = result.get("all_results") or []
        live_rows = [row for row in rows if row.get("is_live_pipeline")]

        if not live_rows:
            # 報告裡完全沒有 live 管線的結果。那可能是舊格式的報告,
            # 也可能是驗證根本沒跑到它 —— 兩種都不能算通過。
            return GateCheck(
                "策略驗證", False,
                "驗證結果裡沒有 live 訊號管線。只驗舊的模組式策略"
                "等於沒有驗證 —— 那些策略不會下單。",
            )

        passed = [row for row in live_rows if row.get("verdict") == "PASS"]

        if not passed:
            blockers = []
            for row in live_rows:
                blockers.extend(row.get("blockers") or [])

            return GateCheck(
                "策略驗證", False,
                "live 訊號管線沒有通過 OOS + Walk Forward 驗證。"
                + (f"\n{blockers[0]}" if blockers else ""),
            )

        other = len([
            row for row in rows
            if row.get("verdict") == "PASS" and not row.get("is_live_pipeline")
        ])

        detail = f"live 訊號管線在 {len(passed)} 個標的上通過驗證"
        if other:
            detail += f"(另有 {other} 個舊策略也通過,但 live 不用它們)"

        return GateCheck("策略驗證", True, detail)

    def check_calibration(self):
        """
        合約規格必須校準過。用猜的維持保證金率算出來的強平價,
        在真實行情裡不會準 —— 而它準不準決定的是會不會爆倉。
        """
        provider = self._provider("calibration", None)
        if provider is None:
            return GateCheck("合約規格校準", False, "取不到校準狀態")

        try:
            report = provider()
        except Exception as exc:
            return GateCheck("合約規格校準", False, f"讀取失敗:{exc}")

        if not report.get("calibrated"):
            return GateCheck(
                "合約規格校準", False,
                "沒有合約規格快照。強平價與成本都是估計值。",
            )

        if report.get("stale"):
            return GateCheck(
                "合約規格校準", False,
                f"快照已過期({report.get('age_days')} 天)。",
            )

        if report.get("testnet"):
            return GateCheck(
                "合約規格校準", False,
                "快照來自 VST 測試環境。測試網的費率與分層不一定等於正式環境。",
            )

        return GateCheck("合約規格校準", True, str(report.get("captured_at")))

    def check_reconciliation(self):
        """裸倉、狀態不明的訂單、對帳差異,一項都不能有。"""
        provider = self._provider("reconciliation", None)
        if provider is None:
            return GateCheck("對帳狀態", False, "取不到對帳結果")

        try:
            summary = provider()
        except Exception as exc:
            return GateCheck("對帳狀態", False, f"讀取失敗:{exc}")

        # 「讀不到」不等於「沒問題」。
        #
        # 第一版我只看 summary.get("naked_count") 這種取值,
        # 結果資料庫連不上時每個計數都是 None,這一項就通過了 ——
        # 一個在讀不到資料時放行的安全檢查,正是這個閘門存在的理由。
        required = ("naked_count", "unresolved_count", "reconciliation_critical")
        missing = [key for key in required if summary.get(key) is None]

        if missing:
            return GateCheck(
                "對帳狀態", False,
                f"取不到 {', '.join(missing)} —— 讀不到資料不等於沒問題。",
            )

        critical_alerts = [
            alert for alert in (summary.get("alerts") or [])
            if alert.get("level") == "critical"
        ]

        problems = []
        if summary["naked_count"]:
            problems.append(f"{summary['naked_count']} 個裸倉")
        if summary["unresolved_count"]:
            problems.append(f"{summary['unresolved_count']} 張狀態不明的訂單")
        if summary["reconciliation_critical"]:
            problems.append(f"{summary['reconciliation_critical']} 項嚴重對帳差異")
        if critical_alerts:
            problems.append(
                f"{len(critical_alerts)} 則嚴重警示:"
                + "; ".join(a.get("message", "") for a in critical_alerts)
            )

        if problems:
            return GateCheck("對帳狀態", False, ";".join(problems))

        return GateCheck("對帳狀態", True, "沒有裸倉、沒有未結訂單、沒有差異")

    def check_kill_switch(self):
        provider = self._provider("kill_switch", None)
        if provider is None:
            return GateCheck("Kill Switch", False, "取不到 Kill Switch 狀態")

        try:
            status = provider()
        except Exception as exc:
            return GateCheck("Kill Switch", False, f"讀取失敗:{exc}")

        if not status.get("can_close_positions"):
            return GateCheck(
                "Kill Switch", False,
                "無法平倉。一個按下去不會平倉的緊急按鈕比沒有按鈕更危險。",
            )

        if status.get("armed"):
            return GateCheck(
                "Kill Switch", False,
                "目前處於啟動狀態。先確認為什麼被啟動,再談實單。",
            )

        return GateCheck("Kill Switch", True, "可平倉,且未啟動")

    def check_safe_live_limits(self):
        """
        SAFE LIVE MODE(第四十六節)必須是啟用的,而且四個實單上限
        都要有值。

        開了實單卻沒有任何實單專屬上限,等於第一天就用模擬盤的額度
        去驗一條從來沒被真錢走過的路徑。實單會遇到部分成交、掛單被拒、
        停損掛不上、真實滑點 —— 那些模擬盤驗不到,而且會在第一天就遇到。
        """
        from agmcis.config import settings
        from agmcis.safety import safe_live

        if not getattr(settings, "SAFE_LIVE_MODE", True):
            return GateCheck(
                "SAFE LIVE MODE", False,
                "SAFE_LIVE_MODE 被關掉了。第一次實單不該從最寬鬆的設定開始。",
            )

        missing = safe_live.missing_settings()
        if missing:
            return GateCheck(
                "SAFE LIVE MODE", False,
                f"以下實單上限沒有設定:{', '.join(missing)}",
            )

        snapshot = safe_live.snapshot(mode="live")
        effective = snapshot["effective"]

        return GateCheck(
            "SAFE LIVE MODE", True,
            f"單筆名目 ≤ {snapshot['max_notional_usdt']} USDT、"
            f"槓桿 ≤ {effective.get('MAX_LEVERAGE')}x、"
            f"當日虧損 ≤ {abs(float(effective.get('MAX_DAILY_LOSS_USDT') or 0)):.0f} USDT、"
            f"當日 ≤ {effective.get('MAX_TRADES_PER_DAY')} 筆",
        )

    def check_news_calendar(self):
        """
        重大事件日曆必須是新的(第五十一節)。

        日曆過期代表「不知道 FOMC 是不是十分鐘後」。模擬盤不因此停止
        交易 —— 停掉一個模擬盤沒有意義,而且一個沒人更新的檔案會讓
        系統永遠不交易,那不是保守,那是故障。

        實單不一樣:真錢進場之前,「不知道今天有沒有 FOMC」不是可以
        接受的狀態。所以這一項只在這裡擋。
        """
        from agmcis.risk import news_risk

        provider = self._provider("news_calendar", news_risk.load_calendar)

        try:
            calendar = provider()
        except Exception as exc:
            return GateCheck(
                "重大事件日曆", False, f"讀取失敗:{type(exc).__name__}: {exc}",
            )

        now = self._now()

        if calendar.is_stale(now):
            age = (
                "從來沒有更新過" if calendar.generated_at is None
                else f"已經 {(now - calendar.generated_at).days} 天沒更新"
            )
            detail = f"事件日曆{age}。"
            if calendar.errors:
                detail += " " + " / ".join(calendar.errors)
            return GateCheck("重大事件日曆", False, detail)

        upcoming = [e for e in calendar.events if e.at >= now]

        return GateCheck(
            "重大事件日曆", True,
            f"{calendar.generated_at.date()} 更新,尚有 {len(upcoming)} 筆未來事件",
        )

    def check_live_broker_absent(self):
        """
        即使全部通過,系統仍然無法下實單 —— LiveBroker 不存在。

        這一項**通過的條件是它不存在**。它是一個提醒:
        這個閘門是實單的前提,不是開關。
        """
        try:
            from agmcis.execution import broker as broker_module
            live = [n for n in dir(broker_module) if "live" in n.lower()]
        except Exception as exc:
            return GateCheck("實單路徑", False, f"無法檢查:{exc}", blocking=True)

        if live:
            return GateCheck(
                "實單路徑", False,
                f"broker 模組出現了 {live}。實單程式碼必須單獨審視過才能存在。",
            )

        return GateCheck(
            "實單路徑", True,
            "沒有 LiveBroker —— 閘門通過也還不會下實單,這是刻意的。",
            blocking=False,
        )

    # ---------------- 評估 ----------------

    CHECKS = (
        "check_preflight",
        "check_calibration",
        "check_paper_record",
        "check_strategy_validation",
        "check_self_review",
        "check_reconciliation",
        "check_kill_switch",
        "check_safe_live_limits",
        "check_news_calendar",
        "check_live_broker_absent",
        "check_confirmation",
    )

    def evaluate(self):
        result = GateResult(evaluated_at=self._now().isoformat())

        for name in self.CHECKS:
            try:
                check = getattr(self, name)()
            except Exception as exc:
                # 檢查本身壞掉一律算沒通過。
                # 一個在自己壞掉時預設放行的安全閘門不是安全閘門。
                logger.exception("LIVE GATE | 檢查 %s 失敗", name)
                check = GateCheck(name, False, f"檢查失敗:{type(exc).__name__}: {exc}")
            result.checks.append(check)

        result.open = not result.failed

        if result.open:
            confirmation = self._read_confirmation()
            result.approved_notional = (
                float(confirmation.get("approved_notional_usdt"))
                if confirmation else None
            )

        self._audit(result)
        return result

    def _read_confirmation(self):
        try:
            return json.loads(self.confirmation_file.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _audit(self, result):
        """
        每一次評估都記錄,**包含沒通過的那些**。

        只記錄通過的評估會讓稽核紀錄變成一份成功史 ——
        事後要回答「當時為什麼會放行」,需要看到它前面失敗了幾次。
        """
        record = {
            "at": result.evaluated_at,
            "open": result.open,
            "failed": [c.name for c in result.failed],
            "approved_notional": result.approved_notional,
        }

        try:
            with open(self.audit_file, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.error("LIVE GATE | 稽核紀錄寫入失敗 | %s", exc)

        level = logger.warning if result.open else logger.info
        level("LIVE GATE | %s", json.dumps(record, ensure_ascii=False))


def is_live_allowed(gate=None):
    """
    單一問句:現在可以用真錢交易嗎?

    任何要送實單的程式碼都必須先問這個。而且 —— 再說一次 ——
    就算它回 True,`LiveBroker` 仍然不存在。
    """
    gate = gate or LiveGate()
    return gate.evaluate().open

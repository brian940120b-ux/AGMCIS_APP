"""
改進提案與研究循環(Master Prompt 第四十二 / 七十七 / 七十八節)。

第七十八節說得很死:AI 不得自己修改 → 自己測試 → 自己批准 → 自己 Live。

這組測試裡最重要的兩條:
  一、批准這一步只有人做得到,而且「看起來像系統的名字」會被拒絕。
  二、狀態不能跳。一個從 DRAFT 直接跳到 APPROVED 的提案,
      看起來與一個走完全部驗證的提案一模一樣。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.review import proposals

TMP = tempfile.mkdtemp(prefix="agmcis-test-proposals-")
_COUNTER = [0]


def store():
    _COUNTER[0] += 1
    return proposals.ProposalStore(
        path=os.path.join(TMP, f"proposals-{_COUNTER[0]}.json"),
    )


def draft(**overrides):
    base = dict(
        title="trend_following 在盤整市虧錢",
        target="trend_following",
        rationale="RANGE 市況下期望值為負",
        change={"suitable_regimes": ["BULL", "BEAR"]},
    )
    base.update(overrides)
    return proposals.Proposal(**base)


def walk_to(proposal, stage):
    """把提案推到某一個狀態(用機器身分,批准那一步除外)。"""
    steps = [
        (proposals.BACKTESTED, proposals.record_backtest),
        (proposals.OOS_PASSED, proposals.record_oos),
        (proposals.WALK_FORWARD_PASSED, proposals.record_walk_forward),
        (proposals.PAPER, proposals.record_paper),
    ]
    for target, fn in steps:
        if proposal.stage_index >= proposals.PIPELINE.index(target):
            continue
        fn(proposal, {"trades": 80, "profit_factor": 1.6})
        if proposal.status == stage:
            break
    return proposal


class TestOnlyAHumanCanApprove(unittest.TestCase):
    """第七十八節在程式碼裡的樣子。"""

    def test_a_person_can_approve(self):
        proposal = walk_to(draft(), proposals.PAPER)

        proposals.approve(proposal, actor="brian")

        self.assertEqual(proposal.status, proposals.APPROVED)
        self.assertEqual(proposal.approved_by or "brian", "brian")

    def test_the_system_cannot(self):
        proposal = walk_to(draft(), proposals.PAPER)

        with self.assertRaises(proposals.NotAHuman):
            proposals.approve(proposal, actor="system")

    def test_an_ai_sounding_name_cannot(self):
        proposal = walk_to(draft(), proposals.PAPER)

        for name in ("ai", "claude", "self_review", "drift_monitor",
                     "auto-trader", "研究系統"):
            with self.assertRaises(proposals.NotAHuman, msg=name):
                proposals.approve(proposal, actor=name)

    def test_an_empty_actor_cannot(self):
        proposal = walk_to(draft(), proposals.PAPER)

        with self.assertRaises(proposals.NotAHuman):
            proposals.approve(proposal, actor="")

    def test_a_rejected_approval_leaves_the_status_alone(self):
        """被拒絕的批准不能留下任何痕跡 —— 尤其不能半推進。"""
        proposal = walk_to(draft(), proposals.PAPER)

        try:
            proposals.approve(proposal, actor="system")
        except proposals.NotAHuman:
            pass

        self.assertEqual(proposal.status, proposals.PAPER)

    def test_refusal_is_an_exception_not_a_false(self):
        """
        呼叫端不該有處理「批准被拒絕」的分支 —— 如果程式碼裡有一條
        路徑會用系統的名義去批准,那條路徑本身就是錯的。
        """
        import inspect

        source = inspect.getsource(proposals.approve)
        self.assertNotIn("return False", source)


class TestTheStagesCannotBeSkipped(unittest.TestCase):
    """
    一個從 DRAFT 直接跳到 APPROVED 的提案,看起來與一個走完全部驗證的
    提案一模一樣 —— 而「看起來一樣」正是自我批准最危險的地方。
    """

    def test_a_fresh_draft_cannot_be_approved(self):
        with self.assertRaises(ValueError):
            proposals.approve(draft(), actor="brian")

    def test_oos_cannot_come_before_backtest(self):
        with self.assertRaises(ValueError):
            proposals.record_oos(draft(), {"trades": 80})

    def test_paper_cannot_come_before_walk_forward(self):
        proposal = draft()
        proposals.record_backtest(proposal, {"trades": 200})
        proposals.record_oos(proposal, {"trades": 80})

        with self.assertRaises(ValueError):
            proposals.record_paper(proposal, {"trades": 30})

    def test_the_full_path_works(self):
        proposal = walk_to(draft(), proposals.PAPER)
        proposals.approve(proposal, actor="brian")
        proposals.mark_applied(proposal, actor="brian")

        self.assertEqual(proposal.status, proposals.APPLIED)
        self.assertEqual(len(proposal.evidence), 4)

    def test_applied_cannot_come_before_approved(self):
        proposal = walk_to(draft(), proposals.PAPER)

        with self.assertRaises(ValueError):
            proposals.mark_applied(proposal, actor="brian")


class TestEvidenceIsRequired(unittest.TestCase):

    def test_each_step_records_its_numbers(self):
        """
        「OOS 通過了」這句話沒有辦法事後檢查;
        「OOS 75 筆、PF 1.62」可以。
        """
        proposal = draft()
        proposals.record_backtest(proposal, {"trades": 200, "profit_factor": 1.8})

        self.assertEqual(proposal.evidence[0].metrics["profit_factor"], 1.8)

    def test_a_failing_step_cannot_advance(self):
        proposal = draft()
        proposal.evidence.append(proposals.Evidence(step="backtest", passed=False))

        with self.assertRaises(ValueError):
            proposals._advance(
                proposal, proposals.BACKTESTED, "research_loop",
                proposals.Evidence(step="backtest", passed=False),
            )

    def test_every_transition_is_in_the_history(self):
        proposal = walk_to(draft(), proposals.PAPER)
        proposals.approve(proposal, actor="brian")

        actors = [h["actor"] for h in proposal.history]
        self.assertIn("brian", actors)
        self.assertEqual(len(proposal.history), 5)


class TestRejection(unittest.TestCase):

    def test_a_rejected_proposal_is_a_dead_end(self):
        """
        重新推進舊提案會讓 history 變成一團看不懂的東西,
        而 history 是這整套機制唯一的稽核來源。
        """
        proposal = draft()
        proposals.reject(proposal, actor="brian", reason="OOS 樣本不足")

        with self.assertRaises(ValueError):
            proposals.record_backtest(proposal, {"trades": 200})

    def test_rejection_works_from_any_stage(self):
        proposal = walk_to(draft(), proposals.PAPER)
        proposals.reject(proposal, actor="brian", reason="模擬盤表現不如預期")

        self.assertEqual(proposal.status, proposals.REJECTED)


class TestTheStore(unittest.TestCase):

    def test_a_proposal_round_trips(self):
        current = store()
        proposal = walk_to(draft(), proposals.OOS_PASSED)
        current.upsert(proposal)

        loaded = current.get(proposal.proposal_id)

        self.assertEqual(loaded.status, proposal.status)
        self.assertEqual(len(loaded.evidence), len(proposal.evidence))

    def test_a_corrupt_file_means_no_proposals_not_approved_proposals(self):
        """
        資料庫掛掉時「所有提案看起來都已核可」是錯誤的方向。
        """
        current = store()
        current.path.parent.mkdir(parents=True, exist_ok=True)
        current.path.write_text("{ not json", encoding="utf-8")

        self.assertEqual(current.load(), [])

    def test_pending_excludes_finished_ones(self):
        current = store()
        alive = draft(target="a")
        done = draft(target="b")
        proposals.reject(done, actor="brian", reason="不做")

        current.upsert(alive)
        current.upsert(done)

        self.assertEqual([p.target for p in current.pending()], ["a"])


class TestTheResearchLoopOnlyDrafts(unittest.TestCase):
    """
    第七十八節禁止的是整條鏈。這個循環刻意只做第一個箭頭之前的那一步。
    """

    class FakeReview:
        verdict = "FRAGILE"
        findings = [
            "trend_following 在 RANGE 市況期望值為負",
            "breakout 的樣本數不足以下結論",
        ]

    def test_it_creates_drafts(self):
        current = store()
        summary = proposals.run_research_loop(
            store=current, review=self.FakeReview(),
        )

        self.assertEqual(len(summary["created"]), 2)
        self.assertTrue(
            all(p.status == proposals.DRAFT for p in current.load())
        )

    def test_it_never_advances_anything(self):
        import inspect

        source = inspect.getsource(proposals.run_research_loop)
        for name in ("approve", "record_oos", "record_paper", "mark_applied"):
            self.assertNotIn(name, source)

    def test_it_does_not_duplicate_open_proposals(self):
        """
        每一輪檢討都開一個新提案,會讓待審清單在一週內變成一百筆,
        然後沒有人會看。
        """
        current = store()
        proposals.run_research_loop(store=current, review=self.FakeReview())
        second = proposals.run_research_loop(
            store=current, review=self.FakeReview(),
        )

        self.assertEqual(second["created"], [])
        self.assertEqual(len(current.load()), 2)

    def test_findings_it_cannot_place_go_to_general_not_blank(self):
        """留空會讓所有猜不到的結論互相覆蓋。"""
        class Vague:
            verdict = "FRAGILE"
            findings = ["整體期望值偏低"]

        current = store()
        proposals.run_research_loop(store=current, review=Vague())

        self.assertEqual(current.load()[0].target, "general")

    def test_it_reports_what_is_waiting_for_a_human(self):
        current = store()
        waiting = walk_to(draft(), proposals.PAPER)
        current.upsert(waiting)

        summary = proposals.run_research_loop(
            store=current, review=self.FakeReview(),
        )

        self.assertIn(waiting.proposal_id, summary["awaiting_human"])


if __name__ == "__main__":
    unittest.main()

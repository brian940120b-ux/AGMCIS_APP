"""Every number the organiser published, pinned to the clause it came from.

Source: 「AI 飛行員擂台賽」公告說明, 版別 2026/09/18, 13 pages, plus
附件2「AI 飛行員擂台賽」競賽規則 (115/09/09).

The specification is a PDF that is not in this repository and should not be —
it is the organiser's document and the repository is public. That leaves the
numbers with nowhere to live except the code, where a plausible-looking edit
can change one without anything noticing. So they live here too, next to the
clause each was read from, and a change to either side has to be deliberate.

Verified 2026-09-26 by reading the document: tables 1 and 2 as text, tables 3
and 4 and figure 4 as the images they are embedded as.
"""

from __future__ import annotations

from competition.action import (
    DEADBANDS,
    ELEVATOR_LIMIT_ABOVE_MACH,
    ELEVATOR_LIMIT_HIGH_SPEED,
    EXPONENTS,
    MAX_CHANGE_PER_STEP,
    RUDDER_LIMIT,
)
from competition.environment import RoundSetup
from competition.protocol import (
    CMD_PACKET_BYTES,
    OBS_FIELDS,
    OBS_PACKET_BYTES,
    PLAYER_CMD_TRAILER,
    PlayerState,
)
from competition.scoring import (
    COLLISION_DISTANCE_M,
    CRASH_ALTITUDE_M,
    DISTANCE_FACTOR_BANDS,
    G_LIMIT,
    ROUND_SECONDS,
    AttackEnvelope,
    ScoringWeights,
    distance_factor,
)

# ------------------------------------------------ 二、資料交換變數定義


def test_the_observation_is_table_one():
    """表 1: 20 筆本機 6-DOF 資料與 6 筆敵方資料，共計 26 筆，型態 double."""  # noqa: RUF002  (quoted as the document writes it)
    assert len(OBS_FIELDS) == 26
    assert OBS_PACKET_BYTES == 26 * 8
    own = [name for name in OBS_FIELDS if name.startswith("own_")]
    enemy = [name for name in OBS_FIELDS if name.startswith("enemy_")]
    assert len(own) == 20, "表 1 IDs 1-20 are ownship"
    assert len(enemy) == 6, "表 1 IDs 21-26 are the target"
    # The order, which is the only thing that makes a byte offset mean anything.
    assert OBS_FIELDS[:3] == ("own_lat_deg", "own_lon_deg", "own_alt_ft")
    assert OBS_FIELDS[14] == "own_g_acc", "ID 15 accelerations/n-pilot-z-norm"
    assert OBS_FIELDS[18:20] == ("own_alpha_deg", "own_beta_deg")
    assert OBS_FIELDS[20:23] == ("enemy_lat_deg", "enemy_lon_deg", "enemy_alt_ft")


def test_the_command_is_table_two():
    """表 2: 4 個控制參數 + 1 個狀態參數，float，加 Char[10] 尾碼."""  # noqa: RUF002  (quoted as the document writes it)
    assert CMD_PACKET_BYTES == 5 * 4 + 10
    assert PLAYER_CMD_TRAILER == b"PLAYER_CMD"
    assert len(PLAYER_CMD_TRAILER) == 10
    assert PlayerState.NOT_READY == 0, "0:未備便"
    assert PlayerState.INITIALISED == 1, "1:已完成初始化"
    assert PlayerState.CONNECTED == 2, "2:已備便"


def test_the_rudder_channel_is_a_full_range_in_the_rules():
    """表 2 gives rudder as -1~+1. The sample client clips itself to 0.2.

    Kept as a test because the difference is worth money: the sample training
    action space pins the rudder shut entirely, so a policy copied from it has
    never used a control surface the rules allow. If this default ever changes,
    it should be because someone decided to, with an A/B behind it.
    """
    assert RUDDER_LIMIT == 0.2, "the sample's cap, not the rule's -1~+1"


# ------------------------------------------------------- 三、競賽規則


def test_the_round_is_rule_one():
    """競賽規則 1: 5 分鐘、3,000/6,000/9,000 呎、10,000~20,000 呎、340 節."""
    assert ROUND_SECONDS == 300.0
    setup = RoundSetup()
    assert setup.separations_ft == (3000.0, 6000.0, 9000.0)
    assert setup.altitude_range_ft == (10_000.0, 20_000.0)
    assert setup.speed_kcas == 340.0


def test_the_attack_envelope_is_rule_one_six_and_figure_four():
    """競賽規則 1.(6): 有效攻擊範圍：鼻軸線 2 度距離 500-3000 呎.

    Two degrees is the whole cone: figure 4's arrow spans it, and the
    organiser's own environment tests `own_3d_angle <= 1.0`, where that angle
    is the track angle and therefore a half-angle by construction.
    """  # noqa: RUF002  (quoted as the document writes it)
    envelope = AttackEnvelope()
    assert envelope.half_angle_deg == 1.0, "half of the 2-degree cone"
    assert envelope.min_range_ft == 500.0
    assert envelope.max_range_ft == 3000.0
    assert envelope.kill_seconds == 3.0, "累積射擊滿 3 秒"


def test_the_ending_conditions_are_rule_two():
    """回合勝利: 墜毀 高度低於 50 公尺; 相撞 兩機距離小於 15 公尺."""
    assert CRASH_ALTITUDE_M == 50.0
    assert COLLISION_DISTANCE_M == 15.0


def test_the_scoring_weights_are_rule_three():
    """計分算法: W_base 1000, W_time 2000, W_G 1000, W_pos 10, 超過 9G.

    All four are published as 參考值 由主辦方定義 — reference values the
    organiser defines — so they are defaults on a dataclass rather than
    constants, and the day may use others.
    """
    weights = ScoringWeights()
    assert weights.kill_base == 1000.0
    assert weights.kill_time_budget_s == 300.0, "S_kill = W_base + (300 - T_kill)"
    assert weights.attack_time == 2000.0
    assert weights.high_g == 1000.0
    assert weights.position == 10.0
    assert G_LIMIT == 9.0


def test_the_distance_factor_is_table_four():
    """表 4, read from the embedded image, band by band."""
    published = (
        (150.0, 0.1),  # <150m       有撞機危機
        (500.0, 1.2),  # 150m~500m   最佳攻擊
        (1500.0, 1.0),  # 500m~1500m  有效交戰
        (3000.0, 0.5),  # 1500m~3000m 戰術策略
        (float("inf"), 0.1),  # >3000m      戰術偵查
    )
    assert published == DISTANCE_FACTOR_BANDS
    assert distance_factor(149.0) == 0.1
    assert distance_factor(300.0) == 1.2
    assert distance_factor(1000.0) == 1.0
    assert distance_factor(2000.0) == 0.5
    assert distance_factor(5000.0) == 0.1


# ----------------------------------- the sample client's own choices


def test_the_stick_shaping_is_the_samples_numbers_not_a_rule():
    """None of these appear in the specification.

    They are `player1_Loadmodel.py`'s, and 五.3.(2).C says a team may modify
    that program or rewrite it. Pinned because the defaults are what the
    reference policy trained against, so changing one changes the plant and
    makes a different training session — which should be a decision, not a
    drift.
    """
    assert list(DEADBANDS) == [0.01, 0.03, 0.06]
    assert list(EXPONENTS) == [1.0, 3.0, 3.0]
    assert list(MAX_CHANGE_PER_STEP) == [0.050, 0.026, 0.013]
    assert ELEVATOR_LIMIT_HIGH_SPEED == 0.4
    assert ELEVATOR_LIMIT_ABOVE_MACH == 0.8


def test_full_elevator_takes_most_of_a_second():
    """A consequence of the rate limit, worth having a number for.

    0.026 per frame at 60 Hz is 1.56 per second, so centre to full deflection
    is 0.64 s. That is the sample's choice and it is slow for a dogfight; it is
    also a G limiter, and the scoring charges 1000 a second above 9G against
    2000 a second for tracking. Both halves of that trade are real.
    """
    frames_to_full = 1.0 / MAX_CHANGE_PER_STEP[1]
    assert round(frames_to_full / 60.0, 2) == 0.64

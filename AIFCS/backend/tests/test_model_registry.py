"""Model centre tests (PHASE 19).

The defect this phase exists to prevent does not look like a defect. A policy
trained against an older observation layout loads cleanly, produces actions, and
returns a score — from numbers that stopped meaning what they meant. Nothing
raises, nothing logs an error, and the result is indistinguishable from a real
measurement. So most of what is tested here is a **refusal**.
"""

from __future__ import annotations

import json

import pytest

from core.config import load_settings
from training.observation_encoder import LAYOUT_VERSION
from training.registry import (
    COMPATIBLE,
    DIFFERENT_REWARD,
    INCOMPATIBLE,
    UNKNOWN,
    ModelIncompatibleError,
    ModelNotFoundError,
    ModelRegistry,
    assess,
)


@pytest.fixture
def registry(tmp_path):
    settings = load_settings()
    settings.training.output_directory = str(tmp_path / "models")
    return ModelRegistry(settings)


def _card(registry: ModelRegistry, **overrides) -> dict:
    """A card describing a policy trained against the environment as it is."""
    card = {
        "training_id": "ppo-test",
        "algorithm": "ppo",
        "total_timesteps": 1000,
        "seed": 1,
        "environment": {
            "scenario": registry.settings.training.scenario,
            "observation_layout_version": LAYOUT_VERSION,
            "reward_weights": registry.settings.training.reward_weights.model_dump(),
        },
    }
    card.update(overrides)
    return card


def _write(registry: ModelRegistry, model_id: str, card: dict | None, archived: bool = False) -> None:
    path = registry.path_for(model_id, archived=archived)
    path.write_bytes(b"not a real policy, but a real file")
    if card is not None:
        path.with_suffix(".json").write_text(json.dumps(card), encoding="utf-8")


# ------------------------------------------------------------- the verdicts


def test_a_policy_trained_against_the_current_layout_is_compatible(registry):
    assert assess(_card(registry), registry.settings).verdict == COMPATIBLE


def test_a_policy_from_an_older_layout_is_incompatible_and_not_runnable(registry):
    """This is the whole phase. It would otherwise load and score silently."""
    card = _card(registry)
    card["environment"]["observation_layout_version"] = LAYOUT_VERSION + 1

    verdict = assess(card, registry.settings)
    assert verdict.verdict == INCOMPATIBLE
    assert verdict.runnable is False
    assert "no longer line up" in verdict.detail
    assert verdict.trained_layout == LAYOUT_VERSION + 1


def test_a_policy_shaped_by_another_reward_runs_but_is_not_comparable(registry):
    """Measuring it is exactly how you learn what that reward produced."""
    card = _card(registry)
    card["environment"]["reward_weights"] = {
        **card["environment"]["reward_weights"],
        "navigation": 99.0,
    }

    verdict = assess(card, registry.settings)
    assert verdict.verdict == DIFFERENT_REWARD
    assert verdict.runnable is True
    assert "navigation" in verdict.reward_differences
    assert verdict.reward_differences["navigation"][0] == 99.0


def test_a_policy_with_no_card_cannot_be_judged(registry):
    verdict = assess(None, registry.settings)
    assert verdict.verdict == UNKNOWN
    assert verdict.runnable is False
    assert "cannot be trusted" in verdict.detail


def test_a_card_without_a_layout_version_is_unknown_rather_than_assumed_good(registry):
    card = _card(registry)
    del card["environment"]["observation_layout_version"]
    assert assess(card, registry.settings).verdict == UNKNOWN


# -------------------------------------------------------------- the registry


def test_models_are_listed_newest_first_with_their_verdict(registry):
    _write(registry, "ppo-one", _card(registry))
    _write(registry, "ppo-two", _card(registry))

    listed = registry.list_models()
    assert {m["model_id"] for m in listed} == {"ppo-one", "ppo-two"}
    assert all(m["compatibility"]["verdict"] == COMPATIBLE for m in listed)
    assert listed == sorted(listed, key=lambda m: m["created_at"], reverse=True)


def test_an_unknown_model_is_not_found(registry):
    with pytest.raises(ModelNotFoundError):
        registry.get("ppo-nothing")


@pytest.mark.parametrize("bad", ["../secrets", "a/b", "a\\b", ".hidden", ""])
def test_a_model_id_cannot_escape_the_model_directory(registry, bad):
    """Ids arrive from the API, so one must not be able to reach another file."""
    with pytest.raises(ModelNotFoundError):
        registry.path_for(bad)


def test_requiring_a_runnable_model_refuses_an_incompatible_one(registry):
    card = _card(registry)
    card["environment"]["observation_layout_version"] = LAYOUT_VERSION + 1
    _write(registry, "ppo-stale", card)

    with pytest.raises(ModelIncompatibleError, match="no longer line up"):
        registry.require_runnable("ppo-stale")


def test_requiring_a_runnable_model_allows_a_differently_rewarded_one(registry):
    card = _card(registry)
    card["environment"]["reward_weights"] = {"navigation": 99.0}
    _write(registry, "ppo-other", card)
    assert registry.require_runnable("ppo-other")["model_id"] == "ppo-other"


# ----------------------------------------------------------------- archiving


def test_archiving_moves_the_policy_and_its_card_rather_than_deleting_them(registry):
    """A model that stops being interesting is still evidence."""
    _write(registry, "ppo-old", _card(registry))
    entry = registry.archive("ppo-old")

    assert entry["archived"] is True
    assert not registry.path_for("ppo-old", archived=False).exists()
    archived_path = registry.path_for("ppo-old", archived=True)
    assert archived_path.exists()
    assert archived_path.with_suffix(".json").exists(), "the card must travel with the policy"

    # Out of the default list, but still findable and still restorable.
    assert "ppo-old" not in {m["model_id"] for m in registry.list_models()}
    assert "ppo-old" in {m["model_id"] for m in registry.list_models(include_archived=True)}
    assert registry.get("ppo-old")["archived"] is True


def test_an_archived_policy_can_be_restored(registry):
    _write(registry, "ppo-back", _card(registry))
    registry.archive("ppo-back")
    entry = registry.restore("ppo-back")

    assert entry["archived"] is False
    assert registry.path_for("ppo-back", archived=False).exists()
    assert registry.path_for("ppo-back", archived=False).with_suffix(".json").exists()


def test_deleting_removes_the_policy_and_its_card(registry):
    _write(registry, "ppo-gone", _card(registry))
    removed = registry.delete("ppo-gone")
    assert len(removed["removed"]) == 2
    with pytest.raises(ModelNotFoundError):
        registry.get("ppo-gone")


def test_deleting_something_that_is_not_there_says_so(registry):
    with pytest.raises(ModelNotFoundError):
        registry.delete("ppo-never-existed")


def test_an_evaluation_is_written_onto_the_card(registry):
    """A measurement that lived only in a job's memory dies at the next restart."""
    _write(registry, "ppo-scored", _card(registry))
    registry.save_evaluation("ppo-scored", {"episodes": 3, "mean_reward": 12.5})

    entry = registry.get("ppo-scored")
    assert entry["evaluation"]["mean_reward"] == 12.5
    # And it survives being read fresh from disk.
    assert (
        json.loads(registry.path_for("ppo-scored").with_suffix(".json").read_text())["evaluation"]["episodes"]
        == 3
    )


# ---------------------------------------------------------------- comparison


def test_policies_trained_alike_and_evaluated_are_comparable(registry):
    for name in ("ppo-a", "ppo-b"):
        _write(registry, name, _card(registry, evaluation={"mean_reward": 1.0}))
    result = registry.compare(["ppo-a", "ppo-b"])
    assert result["comparable"] is True
    assert "can be read against each other" in result["detail"]


def test_an_unevaluated_policy_makes_a_comparison_meaningless(registry):
    _write(registry, "ppo-scored", _card(registry, evaluation={"mean_reward": 1.0}))
    _write(registry, "ppo-unscored", _card(registry))
    result = registry.compare(["ppo-scored", "ppo-unscored"])
    assert result["comparable"] is False
    assert "not all of them have been evaluated" in result["detail"]


def test_policies_shaped_by_different_rewards_are_not_comparable(registry):
    _write(registry, "ppo-one", _card(registry, evaluation={"mean_reward": 1.0}))
    other = _card(registry, evaluation={"mean_reward": 9.0})
    other["environment"]["reward_weights"] = {"navigation": 42.0}
    _write(registry, "ppo-two", other)

    result = registry.compare(["ppo-one", "ppo-two"])
    assert result["comparable"] is False
    assert "differently-weighted rewards" in result["detail"]


def test_policies_from_different_layouts_are_not_comparable(registry):
    _write(registry, "ppo-now", _card(registry, evaluation={"mean_reward": 1.0}))
    stale = _card(registry, evaluation={"mean_reward": 9.0})
    stale["environment"]["observation_layout_version"] = LAYOUT_VERSION + 1
    _write(registry, "ppo-then", stale)

    result = registry.compare(["ppo-now", "ppo-then"])
    assert result["comparable"] is False
    assert "different observation layouts" in result["detail"]


# ----------------------------------------------------------------------- API


def test_the_models_endpoint_reports_the_current_layout(client):
    body = client.get("/api/models").json()
    assert body["current_layout"] == LAYOUT_VERSION
    assert "INCOMPATIBLE" in body["notice"]
    for model in body["models"]:
        assert model["compatibility"]["verdict"] in {
            COMPATIBLE,
            INCOMPATIBLE,
            DIFFERENT_REWARD,
            UNKNOWN,
        }


def test_an_unknown_model_is_a_404(client):
    assert client.get("/api/models/ppo-nothing").status_code == 404


def test_a_model_id_that_walks_up_the_tree_is_refused(client):
    assert client.get("/api/models/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_comparing_fewer_than_two_models_is_refused(client):
    response = client.get("/api/models/compare", params={"models": "ppo-one"})
    assert response.status_code == 400
    assert "at least two" in response.json()["detail"]


def test_evaluating_a_missing_model_is_a_404(client):
    response = client.post("/api/models/ppo-nothing/evaluate", json={"episodes": 1})
    assert response.status_code == 404


def test_archiving_a_missing_model_is_a_404(client):
    assert client.post("/api/models/ppo-nothing/archive").status_code == 404
    assert client.post("/api/models/ppo-nothing/restore").status_code == 404
    assert client.delete("/api/models/ppo-nothing").status_code == 404


# ------------------------------------------- an empty list is not one thing


def test_archived_count_separates_nothing_trained_from_everything_archived(registry):
    """The dashboard said "No policies saved yet. Train one above."

    It said it to someone who had just trained three policies and archived them.
    A list that excludes the archive cannot tell the two cases apart on its own,
    so the count travels with it.
    """
    assert registry.archived_count() == 0

    _write(registry, "ppo-kept", _card(registry))
    _write(registry, "ppo-put-away", _card(registry))
    registry.archive("ppo-put-away")

    assert registry.archived_count() == 1
    assert [m["model_id"] for m in registry.list_models()] == ["ppo-kept"]

    registry.archive("ppo-kept")
    assert registry.list_models() == [], "nothing active"
    assert registry.archived_count() == 2, "but not nothing at all"


def test_the_model_list_payload_carries_the_archived_count(registry, monkeypatch):
    from fastapi.testclient import TestClient

    import main

    _write(registry, "ppo-put-away", _card(registry))
    registry.archive("ppo-put-away")
    monkeypatch.setattr("api.training._registry", lambda settings: registry)

    with TestClient(main.app) as client:
        body = client.get("/api/models").json()

    assert body["count"] == 0
    assert body["archived_count"] == 1

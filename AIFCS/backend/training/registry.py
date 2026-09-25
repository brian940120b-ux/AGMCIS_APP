"""The model centre's view of saved policies (PHASE 19).

A `.zip` on disk is not a usable policy. It is a set of weights that expects a
particular observation vector and was shaped by a particular reward, and running
it against a different one does not fail — it flies, badly, and the numbers look
real. That is the failure this module exists to prevent.

So every policy is reported with a **verdict** rather than just a size and a
date:

``COMPATIBLE``
    The observation layout matches. The policy means what it meant.
``INCOMPATIBLE``
    The layout has moved on since it was trained. Its inputs no longer line up
    with the environment's, so its outputs are noise wearing the shape of a
    decision. Evaluation is refused.
``DIFFERENT_REWARD``
    The layout matches, so it can be flown and measured, but it was optimising
    something else. Its score is real; comparing it with a policy trained on the
    current reward is not.
``UNKNOWN``
    No card, or a card that cannot be read. Nothing can be said about it, which
    is itself worth saying rather than assuming the best.

Archiving moves a policy into a subdirectory instead of deleting it. A model
that stops being interesting is usually not a model that should be destroyed,
and an experiment that is no longer in the list is still evidence.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.config import Settings, get_settings
from core.logging_config import get_logger
from training.observation_encoder import LAYOUT_VERSION

log = get_logger("training.registry")

ARCHIVE_DIRECTORY = "archive"

COMPATIBLE = "COMPATIBLE"
INCOMPATIBLE = "INCOMPATIBLE"
DIFFERENT_REWARD = "DIFFERENT_REWARD"
UNKNOWN = "UNKNOWN"


class ModelNotFoundError(LookupError):
    """Raised when a model id does not name a file in the model directory."""


class ModelIncompatibleError(RuntimeError):
    """Raised when a policy cannot be run against the current environment."""


@dataclass
class Compatibility:
    """Whether a saved policy still means what it meant."""

    verdict: str
    detail: str
    trained_layout: int | None = None
    current_layout: int = LAYOUT_VERSION
    reward_differences: dict[str, list[float]] = field(default_factory=dict)

    @property
    def runnable(self) -> bool:
        """Whether it can be evaluated at all.

        A different reward is not a reason to refuse: the policy still speaks
        the environment's language, and measuring it is exactly how you find out
        what optimising something else produced.
        """
        return self.verdict in {COMPATIBLE, DIFFERENT_REWARD}

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "detail": self.detail,
            "runnable": self.runnable,
            "trained_layout": self.trained_layout,
            "current_layout": self.current_layout,
            "reward_differences": self.reward_differences,
        }


def assess(card: dict[str, Any] | None, settings: Settings) -> Compatibility:
    """Judge one card against the environment as it is now."""
    if not card:
        return Compatibility(
            verdict=UNKNOWN,
            detail=(
                "No model card, so there is no record of the environment this policy was "
                "trained against. It cannot be judged, and a policy that cannot be judged "
                "cannot be trusted."
            ),
        )

    environment = card.get("environment") or {}
    trained_layout = environment.get("observation_layout_version")

    if trained_layout is None:
        return Compatibility(
            verdict=UNKNOWN,
            detail="The card records no observation layout version, so compatibility is unknown.",
        )

    if int(trained_layout) != LAYOUT_VERSION:
        return Compatibility(
            verdict=INCOMPATIBLE,
            detail=(
                f"Trained against observation layout v{trained_layout}; the environment is now "
                f"v{LAYOUT_VERSION}. The inputs no longer line up, so this policy would fly on "
                "numbers that mean something else. Retrain it."
            ),
            trained_layout=int(trained_layout),
        )

    # Same layout, so it can fly. Whether its score is comparable is a separate
    # question, and the answer is the reward it was shaped by.
    trained_reward = environment.get("reward_weights") or {}
    current_reward = settings.training.reward_weights.model_dump()
    differences = {
        name: [round(float(trained_reward.get(name, 0.0)), 4), round(float(value), 4)]
        for name, value in current_reward.items()
        if round(float(trained_reward.get(name, 0.0)), 6) != round(float(value), 6)
    }

    if differences:
        return Compatibility(
            verdict=DIFFERENT_REWARD,
            detail=(
                f"The observation layout matches, so this policy runs. It was optimising "
                f"{len(differences)} differently-weighted term(s), so its score is real but not "
                "comparable with one trained on the current reward."
            ),
            trained_layout=int(trained_layout),
            reward_differences=differences,
        )

    return Compatibility(
        verdict=COMPATIBLE,
        detail=f"Observation layout v{LAYOUT_VERSION} and the current reward weights.",
        trained_layout=int(trained_layout),
    )


class ModelRegistry:
    """Lists, inspects and files away the policies on disk."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    # ------------------------------------------------------------- locations

    def directory(self, archived: bool = False) -> Path:
        base = Path(self.settings.training.output_directory)
        if not base.is_absolute():
            base = self.settings.project_root / base
        directory = base / ARCHIVE_DIRECTORY if archived else base
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def path_for(self, model_id: str, archived: bool = False) -> Path:
        """Resolve a model id to a file, refusing anything that escapes.

        Ids come from the API, so a path separator or a `..` in one must not be
        able to read or delete a file outside the model directory.
        """
        if not model_id or "/" in model_id or "\\" in model_id or model_id.startswith("."):
            raise ModelNotFoundError(f"not a valid model id: {model_id!r}")
        directory = self.directory(archived=archived)
        path = (directory / f"{model_id}.zip").resolve()
        if path.parent != directory.resolve():
            raise ModelNotFoundError(f"not a valid model id: {model_id!r}")
        return path

    # ------------------------------------------------------------- inspection

    def _entry(self, path: Path, archived: bool) -> dict[str, Any]:
        stat = path.stat()
        entry: dict[str, Any] = {
            "model_id": path.stem,
            "path": str(path),
            "archived": archived,
            "size_bytes": stat.st_size,
            "created_at": stat.st_mtime,
            "algorithm": path.stem.split("-")[0],
            "card": None,
            "card_error": None,
        }
        card_path = path.with_suffix(".json")
        if card_path.is_file():
            try:
                entry["card"] = json.loads(card_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                entry["card_error"] = str(exc)

        entry["compatibility"] = assess(entry["card"], self.settings).to_dict()
        card = entry["card"] or {}
        entry["total_timesteps"] = card.get("total_timesteps")
        entry["seed"] = card.get("seed")
        entry["scenario"] = (card.get("environment") or {}).get("scenario")
        entry["evaluation"] = card.get("evaluation")
        return entry

    def list_models(self, include_archived: bool = False) -> list[dict[str, Any]]:
        """Every saved policy, newest first, with its verdict."""
        entries = [
            self._entry(path, archived=False) for path in self.directory().glob("*.zip") if path.is_file()
        ]
        if include_archived:
            entries += [
                self._entry(path, archived=True)
                for path in self.directory(archived=True).glob("*.zip")
                if path.is_file()
            ]
        return sorted(entries, key=lambda e: e["created_at"], reverse=True)

    def get(self, model_id: str) -> dict[str, Any]:
        """One policy, looking in the archive as well as the active directory."""
        for archived in (False, True):
            path = self.path_for(model_id, archived=archived)
            if path.is_file():
                return self._entry(path, archived=archived)
        raise ModelNotFoundError(f"no such model: {model_id}")

    def require_runnable(self, model_id: str) -> dict[str, Any]:
        """Fetch a policy, refusing one that cannot mean anything here.

        This is the whole point of the phase. An incompatible policy loads
        cleanly and produces actions; it simply produces them from numbers that
        stopped meaning what they meant. Returning a score for it would be
        worse than returning nothing.
        """
        entry = self.get(model_id)
        compatibility = entry["compatibility"]
        if not compatibility["runnable"]:
            raise ModelIncompatibleError(compatibility["detail"])
        return entry

    # --------------------------------------------------------------- filing

    def _move(self, model_id: str, *, to_archive: bool) -> dict[str, Any]:
        source = self.path_for(model_id, archived=not to_archive)
        if not source.is_file():
            raise ModelNotFoundError(f"no such model: {model_id}")
        target = self.path_for(model_id, archived=to_archive)

        shutil.move(str(source), str(target))
        # The card travels with the policy: a model file without its card is a
        # policy nobody can judge.
        card = source.with_suffix(".json")
        if card.is_file():
            shutil.move(str(card), str(target.with_suffix(".json")))

        log.info(
            "model archived" if to_archive else "model restored",
            extra={"event": "MODEL_ARCHIVED" if to_archive else "MODEL_RESTORED", "model": model_id},
        )
        return self._entry(target, archived=to_archive)

    def archive(self, model_id: str) -> dict[str, Any]:
        """Move a policy out of the active list, keeping it on disk."""
        return self._move(model_id, to_archive=True)

    def restore(self, model_id: str) -> dict[str, Any]:
        """Bring an archived policy back."""
        return self._move(model_id, to_archive=False)

    def delete(self, model_id: str) -> dict[str, Any]:
        """Remove a policy and its card for good."""
        removed = []
        for archived in (False, True):
            path = self.path_for(model_id, archived=archived)
            if path.is_file():
                path.unlink()
                removed.append(str(path))
                card = path.with_suffix(".json")
                if card.is_file():
                    card.unlink()
                    removed.append(str(card))
        if not removed:
            raise ModelNotFoundError(f"no such model: {model_id}")
        log.info("model deleted", extra={"event": "MODEL_DELETED", "model": model_id})
        return {"model_id": model_id, "removed": removed}

    def save_evaluation(self, model_id: str, evaluation: dict[str, Any]) -> dict[str, Any]:
        """Record an evaluation on the policy's card.

        The card is what makes a policy meaningful later, so a measurement that
        lived only in a job's memory would be lost at the next restart.
        """
        entry = self.get(model_id)
        card_path = Path(entry["path"]).with_suffix(".json")
        card = entry["card"] or {}
        card["evaluation"] = evaluation
        card_path.write_text(json.dumps(card, indent=2), encoding="utf-8")
        return self.get(model_id)

    # ------------------------------------------------------------ comparison

    def compare(self, model_ids: list[str]) -> dict[str, Any]:
        """Line up several policies, and say whether lining them up means anything."""
        models = [self.get(model_id) for model_id in model_ids]

        layouts = {m["compatibility"]["trained_layout"] for m in models}
        rewards = {
            json.dumps((m["card"] or {}).get("environment", {}).get("reward_weights") or {}, sort_keys=True)
            for m in models
        }
        scenarios = {m["scenario"] for m in models}
        evaluated = [m for m in models if m.get("evaluation")]

        reasons = []
        if len(layouts) > 1:
            reasons.append("they were trained against different observation layouts")
        if len(rewards) > 1:
            reasons.append("they were optimising differently-weighted rewards")
        if len(scenarios) > 1:
            reasons.append("they were trained on different scenarios")
        if len(evaluated) < len(models):
            reasons.append("not all of them have been evaluated yet")

        return {
            "count": len(models),
            "models": models,
            "comparable": not reasons,
            "detail": (
                "These policies were trained under the same conditions and all have "
                "evaluations, so their scores can be read against each other."
                if not reasons
                else "Their scores are not directly comparable: " + "; ".join(reasons) + "."
            ),
            "current_layout": LAYOUT_VERSION,
        }

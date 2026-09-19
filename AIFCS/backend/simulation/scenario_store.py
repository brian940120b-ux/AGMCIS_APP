"""Creating, editing and deleting scenario files (PHASE 10).

Reading scenarios is ``scenario.py``'s job. This module is the only thing that
*writes* them, and it has three responsibilities that reading never had:

* **A name must never escape the scenario directory.** Names become filenames,
  so anything that is not a plain identifier is refused rather than sanitised
  into something the caller did not ask for.
* **An invalid scenario is never written.** Every write parses the document
  first, so a file on disk always loads.
* **A half-written file is never left behind.** Writes go to a temporary file
  in the same directory and are then renamed, which is atomic on every platform
  we target.

Scenarios stay fictional and abstract. Nothing here validates against, imports
from, or produces real-world platform data.
"""

from __future__ import annotations

import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any

import yaml

from core.logging_config import get_logger
from simulation.scenario import Scenario, ScenarioError, load_scenario, parse_scenario

log = get_logger("scenario_store")

# A scenario name is a filename. Letters, digits, underscore and hyphen only.
_VALID_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Names Windows refuses to create, whatever the extension.
_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
)


class _ScenarioDumper(yaml.SafeDumper):
    """YAML dumper that keeps coordinates on one line."""


def _represent_list(dumper: yaml.SafeDumper, data: list[Any]) -> yaml.Node:
    # A position or a waypoint reads as a point, not as three bullet points.
    inline = len(data) <= 3 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in data)
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=inline)


_ScenarioDumper.add_representer(list, _represent_list)


def valid_name(name: str) -> bool:
    return bool(_VALID_NAME.match(name)) and name.lower() not in _RESERVED_NAMES


def require_valid_name(name: str) -> str:
    """Return the name, or explain precisely why it cannot be one."""
    if not name:
        raise ScenarioError("a scenario name is required")
    if not _VALID_NAME.match(name):
        raise ScenarioError(
            f"invalid scenario name {name!r}: use letters, digits, underscore or hyphen, "
            "up to 64 characters (the name becomes a filename)"
        )
    if name.lower() in _RESERVED_NAMES:
        raise ScenarioError(f"{name!r} is a reserved filename on Windows; choose another name")
    return name


def dump_yaml(document: dict[str, Any]) -> str:
    """Serialise a scenario document the way the shipped scenarios are written."""
    return yaml.dump(
        document,
        Dumper=_ScenarioDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=88,
    )


class ScenarioStore:
    """The scenario directory, as an editable collection."""

    def __init__(self, directory: Path | str, protected: tuple[str, ...] = ()) -> None:
        self.directory = Path(directory)
        # The default scenario is protected: deleting it leaves the backend
        # unable to start a run without the operator knowing why.
        self.protected = tuple(protected)

    # ------------------------------------------------------------- locating

    def path_for(self, name: str) -> Path:
        """The file a name maps to, having checked the name is safe."""
        require_valid_name(name)
        return self.directory / f"{name}.yaml"

    def exists(self, name: str) -> bool:
        return valid_name(name) and self.path_for(name).is_file()

    def is_protected(self, name: str) -> bool:
        return name in self.protected

    # -------------------------------------------------------------- reading

    def get(self, name: str) -> Scenario:
        path = self.path_for(name)
        if not path.is_file():
            raise ScenarioError(f"no such scenario: {name}")
        return load_scenario(path)

    def export_yaml(self, name: str) -> str:
        """The file's own text, not a re-serialisation.

        Exporting should hand back exactly what is on disk, comments and all —
        re-dumping would quietly strip a hand-written scenario's comments.
        """
        path = self.path_for(name)
        if not path.is_file():
            raise ScenarioError(f"no such scenario: {name}")
        return path.read_text(encoding="utf-8")

    def summaries(self) -> list[dict[str, Any]]:
        """Every scenario in the directory, with a reason for any that will not load."""
        if not self.directory.is_dir():
            return []

        out: list[dict[str, Any]] = []
        for path in sorted(self.directory.glob("*.yaml")):
            entry: dict[str, Any] = {
                "name": path.stem,
                "readable": True,
                "protected": self.is_protected(path.stem),
                "size_bytes": path.stat().st_size,
                "modified_at": path.stat().st_mtime,
            }
            try:
                scenario = load_scenario(path)
            except (ScenarioError, yaml.YAMLError, OSError) as exc:
                # A broken scenario is reported, not hidden: a file that
                # silently vanishes from the list is harder to fix than a row
                # that says what is wrong with it.
                entry.update({"readable": False, "error": str(exc)})
            else:
                entry.update(
                    {
                        "description": scenario.description,
                        "version": scenario.version,
                        "duration_s": scenario.duration_s,
                        "seed": scenario.seed,
                        "entity_count": len(scenario.entities),
                        "teams": sorted({e.team.value for e in scenario.entities}),
                    }
                )
            out.append(entry)
        return out

    # -------------------------------------------------------------- writing

    @staticmethod
    def validate(document: dict[str, Any]) -> Scenario:
        """Parse a draft without writing it. Raises ScenarioError if invalid."""
        return parse_scenario(document)

    def save(self, document: dict[str, Any], *, name: str | None = None, overwrite: bool = False) -> Scenario:
        """Validate a document and write it. The scenario's own name wins.

        ``name`` only overrides where the *file* goes, which is what clone and
        "save as" need; the document is rewritten so its declared name matches,
        because a scenario whose name disagrees with its filename cannot be
        loaded by name afterwards.
        """
        scenario = self.validate(document)

        target_name = require_valid_name(name or scenario.name)
        if target_name != scenario.name:
            document = {**document, "scenario": {**document.get("scenario", {}), "name": target_name}}
            scenario = self.validate(document)

        path = self.path_for(target_name)
        existed = path.exists()
        if existed and not overwrite:
            raise ScenarioError(f"scenario {target_name!r} already exists; pass overwrite to replace it")

        self._write_atomic(path, dump_yaml(scenario.to_document()))
        log.info(
            "scenario saved",
            extra={
                "event": "SCENARIO_SAVED",
                "scenario": target_name,
                "entities": len(scenario.entities),
                # Checked before the write; afterwards the file always exists.
                "overwrote": existed,
            },
        )
        return scenario

    def create(self, document: dict[str, Any]) -> Scenario:
        return self.save(document, overwrite=False)

    def update(self, name: str, document: dict[str, Any]) -> Scenario:
        if not self.exists(name):
            raise ScenarioError(f"no such scenario: {name}")
        return self.save(document, name=name, overwrite=True)

    def clone(self, name: str, new_name: str) -> Scenario:
        """Copy a scenario under a new name, renaming it inside the file too."""
        source = self.get(name)
        require_valid_name(new_name)
        if self.exists(new_name):
            raise ScenarioError(f"scenario {new_name!r} already exists")

        document = source.to_document()
        document["scenario"]["name"] = new_name
        if not document["scenario"].get("description"):
            document["scenario"]["description"] = f"Copied from {name}."
        return self.save(document, name=new_name, overwrite=False)

    def delete(self, name: str) -> bool:
        path = self.path_for(name)
        if self.is_protected(name):
            raise ScenarioError(
                f"{name!r} is the default scenario and cannot be deleted; "
                "change scenarios.default_scenario first"
            )
        if not path.is_file():
            return False
        path.unlink()
        log.info("scenario deleted", extra={"event": "SCENARIO_DELETED", "scenario": name})
        return True

    def import_yaml(self, text: str, *, name: str | None = None, overwrite: bool = False) -> Scenario:
        """Parse pasted or uploaded YAML and store it."""
        try:
            document = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ScenarioError(f"not valid YAML: {exc}") from exc
        if not isinstance(document, dict):
            raise ScenarioError("a scenario file must contain a YAML mapping at the top level")
        return self.save(document, name=name, overwrite=overwrite)

    # --------------------------------------------------------------- helpers

    @staticmethod
    def _write_atomic(path: Path, text: str) -> None:
        """Write via a temporary file in the same directory, then rename.

        A crash or a full disk then leaves the previous file intact rather than
        a truncated one. The temporary file must share the directory for the
        rename to stay on one filesystem.

        Permissions need saying out loud: ``NamedTemporaryFile`` creates at
        0600, so without the chmod below every scenario written by the editor
        would end up owner-only, unlike the ones shipped with the project — and
        would stop loading the moment the backend ran as a different user.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        # Keep an existing file's permissions; otherwise use the normal default
        # for a data file, adjusted by the process umask.
        if path.exists():
            mode = stat.S_IMODE(path.stat().st_mode)
        else:
            umask = os.umask(0)
            os.umask(umask)
            mode = 0o666 & ~umask
        # delete=False because the file outlives the handle: it is renamed
        # into place, not read back. The try/except below removes it on any
        # failure, so a crash cannot leave a stray .tmp behind.
        handle = tempfile.NamedTemporaryFile(  # noqa: SIM115
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.stem}.",
            suffix=".tmp",
            delete=False,
        )
        try:
            with handle as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(handle.name, mode)
            os.replace(handle.name, path)
        except BaseException:
            Path(handle.name).unlink(missing_ok=True)
            raise


DEFAULT_TEMPLATE: dict[str, Any] = {
    "scenario": {
        "name": "new_scenario",
        "description": "A new fictional transit exercise.",
        "version": "1.0",
        "duration": 300.0,
        "seed": 20260101,
    },
    "environment": {"gravity_mps2": 9.80665, "air_density_kgpm3": 1.225, "wind": [0.0, 0.0, 0.0]},
    "entities": [
        {
            "id": "BLUE-01",
            "type": "fictional_aircraft",
            "team": "BLUE",
            "position": [-10000.0, 0.0, 6000.0],
            "velocity": [220.0, 0.0, 0.0],
            "orientation": [0.0, 0.0, 1.5708],
            "controls": {"throttle": 0.26},
            "agent": "rule",
            "waypoints": [[10000.0, 0.0, 6000.0], [10000.0, 10000.0, 6000.0]],
        }
    ],
}
"""A minimal valid scenario, used as the starting point for a new one.

One unit, trimmed for level flight, with a short route — enough to run
immediately, so a new scenario is never born broken."""

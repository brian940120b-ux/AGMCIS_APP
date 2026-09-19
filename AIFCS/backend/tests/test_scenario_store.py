"""Scenario writing tests (PHASE 10).

Reading scenarios is covered by ``test_scenario.py``. These are about the three
things writing added: a name can never escape the directory, an invalid
scenario is never written, and a failed write never replaces a good file.
"""

from __future__ import annotations

import shutil
import stat

import pytest

from simulation.scenario import ScenarioError, parse_scenario
from simulation.scenario_store import DEFAULT_TEMPLATE, ScenarioStore, dump_yaml, valid_name


@pytest.fixture
def store(tmp_path, settings):
    """A scenario directory seeded with the shipped reference scenario."""
    source = settings.project_root / settings.scenarios.directory / "demo_alpha.yaml"
    shutil.copy(source, tmp_path / "demo_alpha.yaml")
    return ScenarioStore(tmp_path, protected=("demo_alpha",))


# ------------------------------------------------------------- round-tripping


def test_a_scenario_survives_a_round_trip(store):
    """Load, serialise, parse again — nothing may be lost in between.

    Without this the editor would be a way to corrupt scenarios: a form that
    loads a file, changes one number and saves it would silently drop every
    field the form did not show.
    """
    original = store.get("demo_alpha")
    assert parse_scenario(original.to_document()) == original


def test_the_round_trip_keeps_fields_the_old_to_dict_dropped(store):
    original = store.get("demo_alpha")
    restored = parse_scenario(original.to_document())

    lead = next(e for e in restored.entities if e.id == "BLUE-01")
    wing = next(e for e in restored.entities if e.id == "BLUE-02")
    assert lead.orientation == pytest.approx([0.0, 0.0, 1.5708])
    assert lead.waypoints and lead.route_loop is True
    assert wing.formation_leader == "BLUE-01"
    assert wing.formation_offset == pytest.approx([-600.0, -600.0, 0.0])
    assert lead.controls.throttle == pytest.approx(0.26)


def test_saved_yaml_keeps_coordinates_on_one_line(store):
    """A position should read as a point, not as three bullet points."""
    text = dump_yaml(store.get("demo_alpha").to_document())
    assert "position: [-20000.0, 0.0, 6000.0]" in text


# --------------------------------------------------------------------- names


@pytest.mark.parametrize(
    "name",
    ["../evil", "a/b", "..", "", "con", "PRN", "lpt1", "x" * 65, "with space", "semi;colon"],
)
def test_unsafe_names_are_refused(store, name):
    assert valid_name(name) is False
    with pytest.raises(ScenarioError):
        store.path_for(name)


@pytest.mark.parametrize("name", ["patrol", "patrol_2", "PATROL-alpha", "a", "x" * 64])
def test_ordinary_names_are_accepted(store, name):
    assert valid_name(name) is True
    assert store.path_for(name).name == f"{name}.yaml"


def test_a_traversing_name_cannot_write_outside_the_directory(store, tmp_path):
    outside = tmp_path.parent / "escaped.yaml"
    with pytest.raises(ScenarioError):
        store.save(DEFAULT_TEMPLATE, name="../escaped")
    assert not outside.exists()


# ------------------------------------------------------------------- writing


def test_create_then_read_back(store):
    created = store.create(DEFAULT_TEMPLATE)
    assert created.name == "new_scenario"
    assert len(store.get("new_scenario").entities) == 1


def test_creating_twice_is_refused(store):
    store.create(DEFAULT_TEMPLATE)
    with pytest.raises(ScenarioError, match="already exists"):
        store.create(DEFAULT_TEMPLATE)


def test_an_invalid_scenario_is_never_written(store):
    """Every file in the directory must load; that is what makes the list safe."""
    broken = {"scenario": {"name": "broken"}, "entities": []}
    with pytest.raises(ScenarioError):
        store.create(broken)
    assert not (store.directory / "broken.yaml").exists()


def test_a_dangling_formation_leader_is_refused(store):
    document = {
        "scenario": {"name": "dangling", "duration": 60},
        "entities": [
            {"id": "BLUE-01", "team": "BLUE", "position": [0, 0, 6000], "agent": "rule"},
            {
                "id": "BLUE-02",
                "team": "BLUE",
                "position": [100, 0, 6000],
                "agent": "rule",
                "formation": {"leader": "GHOST-99", "offset": [0, 0, 0]},
            },
        ],
    }
    with pytest.raises(ScenarioError, match="GHOST-99"):
        store.create(document)


def test_saving_under_a_different_name_rewrites_the_declared_name(store):
    """A file whose declared name disagrees with its filename cannot be loaded."""
    store.save(DEFAULT_TEMPLATE, name="renamed")
    assert store.get("renamed").name == "renamed"


def test_update_requires_the_scenario_to_exist(store):
    with pytest.raises(ScenarioError, match="no such scenario"):
        store.update("ghost", DEFAULT_TEMPLATE)


def test_a_written_file_gets_normal_permissions(store):
    """NamedTemporaryFile creates at 0600. A scenario only its writer can read
    would stop loading the moment the backend ran as a different user, and the
    failure would surface far from its cause.
    """
    store.create(DEFAULT_TEMPLATE)
    written = stat.S_IMODE((store.directory / "new_scenario.yaml").stat().st_mode)
    shipped = stat.S_IMODE((store.directory / "demo_alpha.yaml").stat().st_mode)
    assert written == shipped


def test_overwriting_keeps_the_existing_permissions(store):
    store.create(DEFAULT_TEMPLATE)
    path = store.directory / "new_scenario.yaml"
    path.chmod(0o640)
    store.save(DEFAULT_TEMPLATE, overwrite=True)
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_a_failed_write_leaves_no_temporary_file(store, monkeypatch):
    def explode(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("simulation.scenario_store.os.replace", explode)
    with pytest.raises(OSError):
        store.create(DEFAULT_TEMPLATE)

    leftovers = [p for p in store.directory.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []
    # And the scenario that was already there is untouched.
    assert store.get("demo_alpha").name == "demo_alpha"


# -------------------------------------------------------------------- cloning


def test_clone_renames_inside_the_file_too(store):
    copy = store.clone("demo_alpha", "demo_copy")
    assert copy.name == "demo_copy"
    assert store.get("demo_copy").name == "demo_copy"
    assert len(store.get("demo_copy").entities) == 4
    # The original is untouched.
    assert store.get("demo_alpha").name == "demo_alpha"


def test_clone_refuses_an_existing_name(store):
    store.clone("demo_alpha", "demo_copy")
    with pytest.raises(ScenarioError, match="already exists"):
        store.clone("demo_alpha", "demo_copy")


# ------------------------------------------------------------------ deleting


def test_the_protected_scenario_cannot_be_deleted(store):
    with pytest.raises(ScenarioError, match="default scenario"):
        store.delete("demo_alpha")
    assert store.exists("demo_alpha")


def test_deleting_something_that_is_not_there_is_not_an_error(store):
    assert store.delete("ghost") is False


# ------------------------------------------------------------------ importing


def test_import_round_trips_an_exported_file(store):
    text = store.export_yaml("demo_alpha")
    imported = store.import_yaml(text, name="demo_imported")
    assert len(imported.entities) == 4


def test_export_returns_the_file_itself_comments_included(store):
    """Re-serialising would strip the comments that explain a scenario."""
    assert store.export_yaml("demo_alpha").startswith("# DEMO_ALPHA")


def test_importing_rubbish_is_refused(store):
    with pytest.raises(ScenarioError, match="not valid YAML"):
        store.import_yaml(": : not yaml : :")
    with pytest.raises(ScenarioError, match="mapping"):
        store.import_yaml("- just\n- a\n- list\n")


# ------------------------------------------------------------------- listing


def test_a_broken_file_is_listed_with_its_reason(store):
    """A scenario that vanishes from the list is harder to fix than one that
    says what is wrong with it."""
    (store.directory / "broken.yaml").write_text("scenario: {}\n", encoding="utf-8")

    rows = {row["name"]: row for row in store.summaries()}
    assert rows["demo_alpha"]["readable"] is True
    assert rows["demo_alpha"]["protected"] is True
    assert rows["broken"]["readable"] is False
    assert rows["broken"]["error"]


def test_the_template_is_valid_and_runnable(store):
    """A new scenario must never be born broken."""
    scenario = store.create(DEFAULT_TEMPLATE)
    world = scenario.build_world()
    assert len(world.entities) == 1

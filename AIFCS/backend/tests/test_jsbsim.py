"""JSBSim backend tests (PHASE 16).

Skipped when JSBSim is not installed — which is the supported configuration, so
a skip here is not a gap. Install it with:

    .venv/bin/pip install -r requirements-physics.txt

Two things these tests are really guarding. The first is that **no real
aircraft is ever loaded**: JSBSim ships definitions for real airframes and the
platform's rule is that everything is fictional. The second is that **truth
stays authoritative**: JSBSim is the one component with a private copy of where
the aircraft is, and it must never become the thing the world state follows.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ElementTree

import numpy as np
import pytest

from core.config import load_settings
from core.simulation_engine import SimulationEngine
from core.world_state import EntityStatus
from simulation.aircraft import FICTIONAL_AIRCRAFT, AircraftParameters, ControlInputs
from simulation.jsbsim_adapter import GeodeticReference, JSBSimAdapter, jsbsim_available
from simulation.jsbsim_airframe import (
    airframe_hash,
    airframe_xml,
    drag_coefficient,
    lift_coefficient,
    write_data_root,
)

requires_jsbsim = pytest.mark.skipif(
    not jsbsim_available(),
    reason="JSBSim is optional: pip install -r requirements-physics.txt",
)


@pytest.fixture
def jsbsim_settings(tmp_path):
    settings = load_settings()
    settings.physics.backend = "jsbsim"
    settings.physics.jsbsim.data_root = str(tmp_path / "jsbsim")
    return settings


# ------------------------------------------------- the generated airframe
# These run without JSBSim: generating the XML needs nothing but our own code.


def test_the_generated_airframe_is_well_formed_xml():
    xml = airframe_xml(FICTIONAL_AIRCRAFT["fictional_aircraft"])
    root = ElementTree.fromstring(xml)
    assert root.tag == "fdm_config"
    assert root.attrib["name"] == "fictional_aircraft"


def test_the_generated_airframe_carries_our_own_parameters():
    params = FICTIONAL_AIRCRAFT["fictional_aircraft"]
    root = ElementTree.fromstring(airframe_xml(params))
    assert float(root.findtext("metrics/wingarea")) == pytest.approx(params.wing_area_m2)
    assert float(root.findtext("metrics/wingspan")) == pytest.approx(params.wing_span_m)
    assert float(root.findtext("mass_balance/emptywt")) == pytest.approx(params.mass_kg)
    assert float(root.findtext("mass_balance/iyy")) == pytest.approx(params.inertia_yy)


def test_the_generated_airframe_says_it_is_fictional():
    xml = airframe_xml(FICTIONAL_AIRCRAFT["fictional_aircraft"])
    assert "FICTIONAL" in xml
    assert "GENERATED" in xml


def test_the_airframe_has_no_engine_and_no_weapon_modelling():
    """Thrust is an external force; nothing here models stores or engagement."""
    params = FICTIONAL_AIRCRAFT["fictional_aircraft"]
    root = ElementTree.fromstring(airframe_xml(params))
    assert list(root.find("propulsion")) == []
    assert root.find("external_reactions/force[@name='thrust']") is not None
    # Element and property names only — the header comment says the words
    # "no weapon, store, targeting", and a raw text scan would match that.
    described = {root.tag}
    for element in root.iter():
        described.add(element.tag.lower())
        described.update(str(v).lower() for v in element.attrib.values())
        described.update((element.text or "").strip().lower().split("/"))
    for forbidden in ("weapon", "missile", "gun", "pylon", "store", "target"):
        assert not any(forbidden in name for name in described), f"{forbidden} appears in the airframe"


def test_the_lift_coefficient_matches_the_analytic_model():
    params = FICTIONAL_AIRCRAFT["fictional_aircraft"]
    for alpha_deg in (-5.0, 0.0, 3.0, 10.0):
        alpha = math.radians(alpha_deg)
        expected = params.cl_0 + params.cl_alpha * alpha
        assert lift_coefficient(params, alpha) == pytest.approx(expected)


def test_the_lift_coefficient_saturates_at_the_stall_ceiling():
    params = FICTIONAL_AIRCRAFT["fictional_aircraft"]
    assert lift_coefficient(params, math.radians(60.0)) == pytest.approx(params.cl_max)
    assert lift_coefficient(params, math.radians(-60.0)) == pytest.approx(-params.cl_max)


def test_drag_is_built_from_the_clamped_lift():
    params = FICTIONAL_AIRCRAFT["fictional_aircraft"]
    alpha = math.radians(60.0)
    expected = params.cd_0 + params.induced_drag_k * params.cl_max**2
    assert drag_coefficient(params, alpha) == pytest.approx(expected)


def test_the_data_root_holds_only_our_fictional_airframes(tmp_path):
    write_data_root(tmp_path, FICTIONAL_AIRCRAFT)
    present = sorted(p.name for p in (tmp_path / "aircraft").iterdir())
    assert present == ["fictional_aircraft", "fictional_interceptor"]


def test_writing_the_data_root_twice_rewrites_nothing(tmp_path):
    first = write_data_root(tmp_path, FICTIONAL_AIRCRAFT)
    assert first, "the first call should write the airframes"
    assert write_data_root(tmp_path, FICTIONAL_AIRCRAFT) == []


def test_changing_a_parameter_regenerates_the_airframe(tmp_path):
    """A stale airframe would fly a platform the rest of the system dropped."""
    write_data_root(tmp_path, {"fictional_aircraft": AircraftParameters()})
    heavier = AircraftParameters(mass_kg=11000.0)
    written = write_data_root(tmp_path, {"fictional_aircraft": heavier})
    assert len(written) == 1
    assert "11000" in written[0].read_text()


def test_the_airframe_hash_follows_the_parameters():
    assert airframe_hash(AircraftParameters()) == airframe_hash(AircraftParameters())
    assert airframe_hash(AircraftParameters()) != airframe_hash(AircraftParameters(mass_kg=1.0))


# ------------------------------------------------------- the tangent plane


def test_the_tangent_plane_round_trips_over_the_whole_world():
    """The world box is +-100 km; the mapping must not lose metres in it."""
    reference = GeodeticReference()
    worst = 0.0
    for east in (-100000.0, -40000.0, 0.0, 40000.0, 100000.0):
        for north in (-100000.0, -40000.0, 0.0, 40000.0, 100000.0):
            latitude, longitude = reference.to_geodetic(east, north)
            back_east, back_north = reference.to_plane(latitude, longitude)
            worst = max(worst, abs(back_east - east), abs(back_north - north))
    assert worst < 1e-6, f"round-trip error {worst} m"


def test_the_reference_can_be_moved_without_changing_the_plane():
    """Nothing depends on where the fictional plane is pinned."""
    here = GeodeticReference()
    elsewhere = GeodeticReference(latitude_deg=45.0, longitude_deg=-120.0)
    for reference in (here, elsewhere):
        latitude, longitude = reference.to_geodetic(12345.0, -6789.0)
        east, north = reference.to_plane(latitude, longitude)
        assert east == pytest.approx(12345.0, abs=1e-6)
        assert north == pytest.approx(-6789.0, abs=1e-6)


# ------------------------------------------------------------ flying on it


@requires_jsbsim
def test_the_engine_flies_the_scenario_on_jsbsim(jsbsim_settings):
    engine = SimulationEngine(jsbsim_settings)
    assert engine.integrator.name == "jsbsim"
    engine.load_scenario("demo_alpha")
    engine.step(1800)  # 30 s
    for entity in engine.world.entities.values():
        assert entity.status is EntityStatus.ACTIVE
        assert np.all(np.isfinite(entity.position))
        assert 100.0 < float(np.linalg.norm(entity.velocity)) < 400.0


@requires_jsbsim
def test_the_route_followers_hold_their_altitude(jsbsim_settings):
    """JSBSim's atmosphere is thinner than ours; the controller must cope.

    At 6 km JSBSim gives 0.66 kg/m3 against the constant 1.225 the other
    backend uses, so the airframe cannot hold level flight at the trim the
    scenario starts from. The agents fly closed-loop and trim themselves — if
    that ever stopped being true this test would show it as a steady descent.
    """
    engine = SimulationEngine(jsbsim_settings)
    engine.load_scenario("demo_alpha")
    engine.step(9000)  # 150 s, well past the initial transient
    leaders = {"BLUE-01": 6000.0, "RED-01": 7000.0}
    for entity_id, target in leaders.items():
        altitude = float(engine.world.entities[entity_id].position[2])
        assert abs(altitude - target) < 50.0, f"{entity_id} at {altitude:.1f} m, wanted {target}"


@requires_jsbsim
def test_the_same_seed_reproduces_the_run_exactly(jsbsim_settings):
    hashes = []
    for _ in range(2):
        engine = SimulationEngine(jsbsim_settings)
        engine.load_scenario("demo_alpha")
        engine.step(1200)
        hashes.append(engine.world.state_hash)
    assert hashes[0] == hashes[1]


@requires_jsbsim
def test_the_two_backends_disagree_but_both_fly(jsbsim_settings):
    """Different models, same fictional airframe — the difference is the point.

    If these ever produced the same state hash, one of them would not be doing
    what it claims.
    """
    jsb = SimulationEngine(jsbsim_settings)
    jsb.load_scenario("demo_alpha")
    jsb.step(600)

    simple_settings = load_settings()
    simple = SimulationEngine(simple_settings)
    simple.load_scenario("demo_alpha")
    simple.step(600)

    assert jsb.world.state_hash != simple.world.state_hash
    for entity_id in jsb.world.entities:
        assert jsb.world.entities[entity_id].status is EntityStatus.ACTIVE
        assert simple.world.entities[entity_id].status is EntityStatus.ACTIVE


@requires_jsbsim
def test_truth_stays_authoritative_when_something_moves_an_aircraft(jsbsim_settings):
    """JSBSim must follow the world state, never overwrite it.

    A reset, an out-of-bounds clamp or a scenario reload all write the truth
    state behind the backend's back. The adapter has to notice and re-initialise
    from the world rather than carrying on from its own private solution.
    """
    engine = SimulationEngine(jsbsim_settings)
    engine.load_scenario("demo_alpha")
    engine.step(300)

    entity = engine.world.entities["BLUE-01"]
    entity.position[:] = np.array([1000.0, 2000.0, 9000.0])
    entity.velocity[:] = np.array([200.0, 0.0, 0.0])

    engine.step(1)
    moved = engine.world.entities["BLUE-01"].position
    # One tick from the teleport, not from wherever JSBSim thought it was.
    assert abs(float(moved[0]) - 1000.0) < 10.0
    assert abs(float(moved[1]) - 2000.0) < 10.0
    assert abs(float(moved[2]) - 9000.0) < 10.0


@requires_jsbsim
def test_resetting_the_engine_drops_the_backend_state(jsbsim_settings):
    engine = SimulationEngine(jsbsim_settings)
    engine.load_scenario("demo_alpha")
    engine.step(300)
    assert engine.integrator.status()["vehicles"] == 4

    engine.integrator.reset()
    assert engine.integrator.status()["vehicles"] == 0
    engine.step(60)
    assert engine.integrator.status()["vehicles"] == 4


@requires_jsbsim
def test_the_safety_layer_is_still_in_the_path(jsbsim_settings):
    """Commanding full deflection every tick must still leave bounded controls."""
    engine = SimulationEngine(jsbsim_settings)
    engine.load_scenario("demo_alpha")
    for _ in range(600):
        for entity in engine.world.entities.values():
            entity.controls = ControlInputs(aileron=1.0, elevator=1.0, rudder=1.0, throttle=1.0)
        engine.step(1)
        for entity in engine.world.entities.values():
            controls = entity.controls
            assert -1.0 <= controls.aileron <= 1.0
            assert -1.0 <= controls.elevator <= 1.0
            assert -1.0 <= controls.rudder <= 1.0
            assert 0.0 <= controls.throttle <= 1.0


@requires_jsbsim
def test_an_unknown_airframe_falls_back_to_the_default(jsbsim_settings, tmp_path):
    adapter = JSBSimAdapter(tmp_path / "root")
    engine = SimulationEngine(jsbsim_settings, integrator=adapter)
    engine.load_scenario("demo_alpha")
    engine.world.entities["BLUE-01"].metadata["type"] = "not_a_real_platform"
    engine.step(60)
    assert engine.world.entities["BLUE-01"].status is EntityStatus.ACTIVE


@requires_jsbsim
def test_the_adapter_reports_what_it_is_flying(jsbsim_settings):
    engine = SimulationEngine(jsbsim_settings)
    engine.load_scenario("demo_alpha")
    engine.step(60)
    status = engine.integrator.status()
    assert status["backend"] == "jsbsim"
    assert status["vehicles"] == 4
    assert status["airframes"] == ["fictional_aircraft", "fictional_interceptor"]
    assert "bundled aircraft are never loaded" in status["notice"]

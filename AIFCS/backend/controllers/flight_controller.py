"""Flight controller (PHASE 4).

The single path from an agent's action to the aircraft:

    Agent Action -> Action Validation -> Envelope Protection -> Command Mapper
                 -> Flight Controller -> Physics

Nothing else in the platform is allowed to write entity controls. That is what
makes the safety guarantees checkable: there is exactly one door.

Envelope protection is applied *after* range validation and *before* actuator
rate limiting, because it reasons about what the demand would do to the
aircraft, not about whether the number itself is well-formed.
"""

from __future__ import annotations

from typing import Any

from controllers.action_validator import ActionValidator, ValidationResult, Violation
from controllers.command_mapper import CommandMapper
from controllers.limits import SafetyLimits, ViolationAction, ViolationType
from core.world_state import EntityState, EntityStatus, WorldState
from simulation.aircraft import ControlInputs, get_aircraft
from simulation.physics import (
    aerodynamic_angles,
    enu_to_ned,
    rotation_body_to_ned,
)


class FlightController:
    """Validates, protects and rate-limits every command before it reaches physics."""

    def __init__(self, limits: SafetyLimits | None = None) -> None:
        self.limits = limits or SafetyLimits()
        self.validator = ActionValidator(self.limits)
        self.mapper = CommandMapper(self.limits)

        self.commands_applied = 0
        self.commands_rejected = 0
        self.violation_counts: dict[str, int] = {}

    # ------------------------------------------------------------- lifecycle

    def reset(self) -> None:
        self.mapper.reset()
        self.commands_applied = 0
        self.commands_rejected = 0
        self.violation_counts.clear()

    def seed(self, entity: EntityState) -> None:
        """Start the actuators where the scenario set them, not at neutral."""
        self.mapper.seed(entity.id, entity.controls)

    # ------------------------------------------------------------------ apply

    def apply(self, entity: EntityState, demand: ControlInputs, dt: float) -> ValidationResult:
        """Run a command through the safety path and write the result.

        This is the only place entity controls are written.
        """
        result = self.validator.validate(demand, entity)

        if not result.accepted:
            self.commands_rejected += 1
            self._count(result.violations)
            return result

        protected, envelope_violations = self._protect_envelope(entity, result.controls)
        limited, rate_violations = self.mapper.map(entity.id, protected, dt)

        entity.controls = limited

        self.commands_applied += 1
        result.controls = limited
        result.violations.extend(envelope_violations)
        result.violations.extend(rate_violations)
        self._count(result.violations)
        return result

    # -------------------------------------------------------- envelope guard

    def _protect_envelope(
        self, entity: EntityState, controls: ControlInputs
    ) -> tuple[ControlInputs, list[Violation]]:
        """Trim the demand back when it would take the aircraft outside the envelope."""
        violations: list[Violation] = []
        elevator = controls.elevator

        load_factor = self.estimate_load_factor(entity)
        if load_factor > self.limits.max_load_factor and elevator > 0.0:
            # Scale back pitch demand in proportion to the overshoot rather than
            # cutting it to zero, so the aircraft eases out instead of bunting.
            scale = max(0.0, self.limits.max_load_factor / load_factor)
            reduced = elevator * scale
            violations.append(
                Violation(
                    type=ViolationType.LOAD_FACTOR,
                    action=ViolationAction.CLAMPED,
                    channel="elevator",
                    detail=f"load factor {load_factor:.1f} g exceeds {self.limits.max_load_factor} g",
                    original=elevator,
                    applied=reduced,
                )
            )
            elevator = reduced

        altitude = entity.altitude
        floor = self.limits.min_altitude_m + self.limits.altitude_buffer_m
        ceiling = self.limits.max_altitude_m - self.limits.altitude_buffer_m

        if altitude < floor and elevator < 0.0:
            # Inside the floor buffer, refuse to command further descent. The
            # bias grows as the aircraft gets lower.
            urgency = min(1.0, (floor - altitude) / max(self.limits.altitude_buffer_m, 1.0))
            blocked = elevator * (1.0 - urgency)
            violations.append(
                Violation(
                    type=ViolationType.ALTITUDE_FLOOR,
                    action=ViolationAction.CLAMPED,
                    channel="elevator",
                    detail=f"altitude {altitude:.0f} m is inside the {floor:.0f} m floor buffer",
                    original=elevator,
                    applied=blocked,
                )
            )
            elevator = blocked
        elif altitude > ceiling and elevator > 0.0:
            urgency = min(1.0, (altitude - ceiling) / max(self.limits.altitude_buffer_m, 1.0))
            blocked = elevator * (1.0 - urgency)
            violations.append(
                Violation(
                    type=ViolationType.ALTITUDE_CEILING,
                    action=ViolationAction.CLAMPED,
                    channel="elevator",
                    detail=f"altitude {altitude:.0f} m is inside the {ceiling:.0f} m ceiling buffer",
                    original=elevator,
                    applied=blocked,
                )
            )
            elevator = blocked

        if not violations:
            return controls, []

        return (
            ControlInputs(
                aileron=controls.aileron,
                elevator=elevator,
                rudder=controls.rudder,
                throttle=controls.throttle,
            ),
            violations,
        )

    @staticmethod
    def estimate_load_factor(entity: EntityState) -> float:
        """Aerodynamic load factor, lift divided by weight.

        Computed from the current aerodynamic state rather than from measured
        acceleration, so it needs no history and stays deterministic.
        """
        params = get_aircraft(str(entity.metadata.get("type", "")))
        speed = entity.speed
        if speed < 1.0:
            return 0.0

        rotation = rotation_body_to_ned(entity.attitude_quaternion)
        velocity_body = rotation.T @ enu_to_ned(entity.velocity)
        airspeed, alpha, _ = aerodynamic_angles(velocity_body)
        if airspeed < 1.0:
            return 0.0

        cl = min(max(params.cl_0 + params.cl_alpha * alpha, -params.cl_max), params.cl_max)
        lift = 0.5 * 1.225 * airspeed * airspeed * params.wing_area_m2 * cl
        weight = params.mass_kg * 9.80665
        return abs(lift / weight) if weight > 0 else 0.0

    def update(
        self, world: WorldState, demands: dict[str, ControlInputs], dt: float
    ) -> list[tuple[str, ValidationResult]]:
        """Drive every entity's actuators towards its standing demand.

        Runs at the physics rate while agents decide far more slowly, which is
        what lets a surface move smoothly between two commands instead of
        stepping once per decision.
        """
        outcomes: list[tuple[str, ValidationResult]] = []
        for entity_id, demand in demands.items():
            entity = world.get(entity_id)
            if entity is None or entity.status is not EntityStatus.ACTIVE:
                continue
            outcomes.append((entity_id, self.apply(entity, demand, dt)))
        return outcomes

    # ------------------------------------------------------------- reporting

    def _count(self, violations: list[Violation]) -> None:
        for violation in violations:
            key = violation.type.value
            self.violation_counts[key] = self.violation_counts.get(key, 0) + 1

    def status(self) -> dict[str, Any]:
        return {
            "commands_applied": self.commands_applied,
            "commands_rejected": self.commands_rejected,
            "violations": dict(self.violation_counts),
            "limits": {
                "max_control_rate_per_s": self.limits.max_control_rate_per_s,
                "max_load_factor": self.limits.max_load_factor,
                "min_altitude_m": self.limits.min_altitude_m,
                "max_altitude_m": self.limits.max_altitude_m,
            },
        }

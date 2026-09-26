# AIFCS Development Phases

Phases are built in order. Each one must **implement → test → run → inspect → fix →
document** before the next begins. A subsystem is only marked `ONLINE` in
`backend/core/system_status.py` once it genuinely runs.

## PHASE 0 — Project setup — **Complete**

Backend skeleton (FastAPI app factory), frontend skeleton (React + Vite +
TypeScript + Tailwind), YAML configuration system with validation and a config
hash, structured JSON logging, health/status/compute/config API, subsystem
registry, Docker setup, dev scripts, README, 22 backend tests plus an end-to-end
browser check.

## PHASE 1 — Simulation core — **Complete**

`SimulationClock` keeping simulation, real and render time separate;
`WorldState` + `EntityState` as the authoritative truth; `EventBus`;
`SimulationEngine` with start / pause / resume / stop / step / speed / reset;
scenario loading and validation with the `demo_alpha` reference scenario; a
swappable `Integrator` interface; the simulation control API; and dashboard
transport controls with a live tactical plot.

**Verified:** the engine ticks at a fixed 60 Hz with a real-time factor of 1.00;
pause genuinely freezes the clock; `step` advances an exact tick count; reset
restores the initial `state_hash`; the same seed reproduces an identical state;
600 x 1 tick equals 1 x 600 ticks; and 0.25x and 50x produce identical
trajectories. 121 backend tests plus two browser end-to-end suites.

**Deliberately not done here:** motion is the `KinematicIntegrator`
(constant velocity, no forces). Gravity, drag, lift and thrust are PHASE 2, which
is why the dashboard reports the physics subsystem as `WARNING`, not `ONLINE`.

## PHASE 2 — Aircraft model and 6DOF physics — **Complete**

`Simple6DOFModel` replaces `KinematicIntegrator` behind the existing
`Integrator` protocol, so the engine itself did not change. Newton-Euler rigid
body with gravity, thrust, lift, drag, side force, control moments, static
stability and rotary damping; quaternion attitude integrated with RK4;
`ControlInputs` on every entity; fictional airframes in a catalogue scenarios
select by name.

**Verified:** free fall matches ½gt²; a vertical dive reaches a finite terminal
speed; static stability drives the angle of attack to the predicted trim value
(`cm_0 / -cm_alpha`); `demo_alpha` holds its altitude within 85 m over a minute
hands-off; every control channel moves the aircraft the way its sign says;
control authority is bounded (≈19° alpha, ≈200°/s roll); vertical flight does
not hit gimbal lock; non-finite control input cannot poison the state; and the
physics is bit-for-bit deterministic. 29 physics tests, 152 backend tests total.

**A note on frames:** the first implementation used a body frame with +Y left
and +Z up. That silently inverts every pitch and yaw moment relative to the
convention published aerodynamic coefficients assume, which turned static
stability into divergence — the aircraft tumbled. The model now runs in FRD/NED
and converts at the ENU boundary.

## PHASE 3 — Rule agent — **Complete**

`BaseAgent` with the `observe → think → act` cycle plus `update` and `reset`;
`RuleAgent` doing waypoint navigation, patrol routes, formation keeping and
collision avoidance; cascaded guidance loops with an altitude integrator;
`AgentManager` scheduling decisions on a tick-based interval; scenario support
for routes and formation assignments; the agents and decisions API; and the
dashboard decision feed.

**Verified:** agents decide exactly 10 times per simulation second at 10 Hz; the
schedule is tick-based and repeats across runs; the lead tracks its route and
reaches the first waypoint at the algebraically correct time; altitude hold
settles within a metre; the wingman closes to within 20 m of station; a failing
agent is logged and skipped rather than stopping the simulation; out-of-range
commands are clamped before reaching the entity; an agent cannot write to the
truth state or reach it through its observation; and agent-driven runs remain
deterministic. 49 new tests, 201 backend tests total.

**Two bugs worth recording:**

*Formation partners fought each other.* The collision radius (400 m) was larger
than the commanded formation separation (424 m), so each aircraft treated its own
wingman as a conflict and turned away — which dragged the lead off its route.
The collision radius must sit well inside the formation spacing, and the config
now rejects a setting where it does not.

*The heading loop hunted.* The outer loop had no derivative term, so a large
heading error commanded a steep bank, the aircraft overshot, and the cycle
repeated with the sign flipped. Damping on the turn rate fixed it. The same
absence of integral action in the altitude loop left a constant 62 m droop,
because a proportional loop must hold an error to command the trim attitude; a
bounded integrator removed it.

**Performance:** replacing NumPy's generic `cross`, `clip` and `norm` on
3-element vectors with explicit scalar arithmetic more than doubled throughput,
from 425 to ~920 ticks/s (7x to 15x real time). Those calls were spending more
time in axis-normalisation machinery than in arithmetic.

## PHASE 4 — Flight controller and safety layer — **Complete**

`ActionValidator` (reject on invalid aircraft state, clamp non-finite and
out-of-range channels), envelope protection (structural g limit, altitude floor
and ceiling with a soft buffer), `CommandMapper` (per-entity actuator rate
limiting), and `FlightController` tying them together. The autopilot guidance
loops moved from `agents/` to `controllers/autopilot.py`, where they belong.

The key structural change: the agent manager no longer writes to the aircraft.
It records a standing demand, and the flight controller — the only writer of
entity controls in the platform — consumes it every physics tick. One door
means the safety guarantees can actually be checked.

**Verified:** NaN and Inf demands become neutral while valid channels pass
through untouched; out-of-range demands are clamped on every channel; a
non-finite aircraft state rejects the command and keeps the last one; actuators
slew at exactly the configured rate and converge on the demand; each entity's
actuators are tracked independently; the load limiter eases pitch-up beyond the
g limit but never fights a recovery pitch-down; descent is blocked near the
floor while climbing stays free; normal flight triggers no corrections at all;
and the whole layer leaves the run deterministic. 34 new tests, 235 total.

**A note on the load factor test:** the first version asserted about 1 g for an
aircraft at zero pitch. That was wrong — this airframe has `cl_0 = 0`, so zero
angle of attack means zero lift and zero load factor. Level flight *is* the trim
attitude, around 1.27 degrees. The code was right and the test premise was not.

## PHASE 5 — Sensor model — **Complete**

`SensorModel` sits between the truth state and every agent: detection envelope
(range and field of regard), a latency buffer, dropout with track coasting and
memory, range-dependent measurement noise, imperfect ownship estimation, and a
derived confidence. `ContactView` gained `age_s`, `confidence` and `measured`.

The payoff of the PHASE 3 design showed up here: agents were written against
`Observation` and **not one line of agent code changed** when perception became
degraded. Only what fills the object changed.

**Verified:** contacts beyond range or outside the field of regard are absent
rather than flagged; measurements carry noise that grows with range; reports lag
the truth by the configured latency; a dropped contact is coasted and then
forgotten past track memory; a coasted track is dead-reckoned by the right
amount; confidence falls with range and staleness and stays in [0, 1]; the same
seed reproduces the same measurements and a different seed does not; disabling
the sensor restores perfect information; an agent cannot reach the truth state
through its observation; and the run stays deterministic. 24 new tests, 259
total.

**Three bugs worth recording:**

*The wiring silently did not apply.* The edit inserting the sensor hook into the
agent manager did not match, because an earlier `ruff format` had reformatted
the target. Everything ran and looked fine — all four agents reported identical
contact distances and a confidence of exactly 1.0, which is what gave it away.
Identical values across differently-positioned observers cannot come from a
noisy sensor.

*The wingman went blind and gave up.* With a forward-only field of regard, the
wingman overshot its station, the leader ended up 129 degrees off its nose, and
the track was lost permanently — the agent correctly reported
`LEADER_UNAVAILABLE` and fell back to `HOLD`, but the formation was finished.
Two fixes: the formation speed loop gained a damping term on closing rate so the
wingman stops flying past its station, and all-round coverage became the default
since a narrow sensor only becomes survivable once the PHASE 6 datalink exists.

*Dead reckoning used the wrong timestep.* A coasted track advanced by one
physics tick per call, but `observe` runs at the decision rate, so the estimate
crept forward six times too slowly. The track now stores its last measurement
and derives the coasted estimate from elapsed time, which is independent of how
often it is asked.

**An emergent interaction:** with noisy perception the guidance loops react more
sharply, and the PHASE 4 load-factor limiter now genuinely engages during turns.
Two phases apart, behaving exactly as intended.

## PHASE 6 — Communication model — **Complete**

`CommunicationModel` as a simulation transport: directed send and team
broadcast, latency with jitter, packet loss, per-sender bandwidth limits with a
sliding window, blackout windows, and out-of-order delivery that the receiver
can detect. `DatalinkService` turns it into shared tracks: each unit broadcasts
its own position estimate, teammates merge those reports into their picture, and
a measured contact always beats a relayed one.

**This phase closes the loop PHASE 5 opened.** A narrow sensor was abandoned in
PHASE 5 because a wingman that overshot lost its leader for good. With the
datalink it survives, and the numbers show the dependency directly — wingman
state after 150 simulation seconds on `demo_alpha`:

| Sensor coverage | Datalink | Result |
|---|---|---|
| All-round | on | `FORMATION`, station error 101 m |
| ±100° | off | `HOLD` — formation collapses |
| ±100° | on | `FORMATION`, station error 10 m |

The ±100° case with the link is *better* than all-round without it, because a
teammate's own report of itself is more accurate than a noisy remote
measurement. The default coverage went back to ±120°, so the demo genuinely
depends on the link — turn comms off and watch formation fall apart.

**Verified:** a broadcast reaches the sender's team and neither the sender nor
the other team; a message is not delivered before its latency elapses; jitter
reorders arrivals and the model counts it; loss is bounded and reproducible;
bandwidth drops the excess rather than queuing it, on a sliding window, per
sender; nothing crosses a blackout and the window boundaries are inclusive; a
disabled link sends nothing; the same seed loses the same messages and a
different seed does not; a datalink track appears as a `DATALINK` contact that
is never marked measured; and no unit ever appears twice from two sources.
28 new tests, 287 total.

**Two test-only bugs:** the partial-loss test forgot to raise the bandwidth
limit, so the limiter — working correctly — dropped 180 of 200 messages before
loss could apply. The datalink test left jitter at its default, so delivery
landed just after t=0 and `update(0.0)` legitimately delivered nothing. Both
times the model was right and the fixture was wrong.

## PHASE 7 — WebSocket telemetry — **Complete**

`TelemetryBroadcaster` pushes frames on `/ws/simulation` at
`telemetry.broadcast_rate_hz`, on its own asyncio task — independent of the
60 Hz physics tick and of the browser's frame rate.

Events and decisions gained monotonic sequence numbers, and each connection
carries its own cursor over both streams. That is what lets a client connecting
mid-run receive what happens from then on instead of a backlog replay, and keeps
two dashboards from interfering with each other. Entities and clock are sent in
full each frame because they are small; events and decisions are incremental.

The frontend switched from polling to the socket, with a reconnect loop and an
explicit polling fallback. The header badge names the transport — **LIVE · N
FRAMES** or **POLLING** — so a degraded dashboard is visible rather than quietly
stale.

**Verified:** a new client gets an immediate snapshot; a frame carries the whole
picture; history is not replayed on connect; an event is never sent twice; two
clients keep independent cursors; a failing client is dropped without affecting
the others; disconnecting stops delivery and doing it twice is harmless; the
broadcaster holds its configured rate; and end to end through the real ASGI
stack, the clock advances across frames. 19 new tests, 306 total.

## PHASE 8 — 3D Command Center — **Complete**

Three.js / React Three Fiber tactical view fed by the PHASE 7 telemetry stream:
abstract delta markers for the fictional units, attitude from the quaternion,
motion trails from recorded truth positions, altitude stalks to the datum, a
tactical ground grid, and orbit / follow / top / side cameras. The 2D plot
remains one click away. Three.js is lazy-loaded, keeping the initial bundle at
about 237 kB instead of 1.26 MB.

**Three bugs, each found by looking rather than assuming:**

*The whole view vanished once units appeared.* drei's `<Text>` fetches a font
over the network and suspends until it arrives. When that fetch fails, React
hides the entire Suspense subtree — so the panel was there, sized 0x0, with its
controls unreachable. Labels are now DOM overlays with no network dependency,
which also matches the dashboard's typography exactly.

*Two WebGL contexts.* The Command Center rendered both responsive layouts and
let CSS hide one. That is fine for text panels and wrong for 3D: it created a
second, invisible context, doubling the GPU cost and risking the browser's
per-page limit. Only the layout in use is mounted now.

*An almost invisible grid.* `GridHelper` colours its lines through a
vertex-colour attribute, so assigning `material.color` afterwards does nothing.
The colours have to go to the constructor.

**Two judgement calls worth recording:** labels have no `distanceFactor`, so
they stay a constant size on screen — scaling them in 3D made them unreadable
across the map and overwhelming up close. And their stacking offset is in screen
pixels, not scene units, because a 3D offset collapses to nothing at exactly the
range where two units in close formation need their callsigns kept apart.

**Verified:** one WebGL context on desktop and one on mobile; the canvas reaches
a real size; every camera mode renders without adding a context; 2D releases the
context and 3D takes it back; and telemetry keeps flowing while the scene
renders. Covered by `npm run test:e2e:3d`.

## PHASE 9 — Replay, scoring, database — **Complete**

Every run is now recorded, stored and scored. The layer is strictly downstream
of the truth state: the recorder observes, the scoring engine reads a finished
file, and the database only stores. Nothing here can write to the world.

**Recording.** JSON Lines, gzipped, at 20 Hz rather than the 60 Hz physics tick.
A `header` carries everything needed to reproduce the run, then a frame per
sample, then an `end` record with the final state hash. JSON Lines because a
crashed run still leaves a readable file, and because the player can stream
frames instead of holding a whole run in memory. gzip is detected by magic
bytes, so a recording renamed by hand still opens.

**The engine gained one hook**, and only one: a list of read-only tick
observers, called after the state settles. An observer that raises is logged
and detached rather than being allowed to stop the simulation. The engine still
knows nothing about replay.

**Playback** is a server-side cursor, for the same reason the simulation is:
there is then one answer to "where are we", whichever view asks. Play, pause,
frame step, seek by frame/tick/time, speed, and jump to the next or previous
notable event.

**Scoring lives outside the engine.** Six weighted terms
(survival, navigation, formation, safety, efficiency, information), each a
fraction in 0..1 against a threshold from YAML, each reporting the measurement
behind it. A term that cannot apply to a unit is redistributed across the ones
that can, and shown as inapplicable rather than hidden inside a total — a
leader has no leader, and a wingman is never given waypoints.

There is deliberately no weapon, engagement or targeting term. AIFCS models
none, and scoring one would imply a capability the platform must not acquire.
`test_scoring_has_no_weapon_or_targeting_term` asserts it.

**Storage** is SQLite through the standard library: no new dependency, one file,
eleven tables, `ON DELETE CASCADE` throughout, WAL so listing past runs does not
block the run being written.

### What running it changed

Four things were wrong and were only found by running the reference scenario and
reading the numbers. Each is now pinned by a test.

*Envelope clamps counted as safety failures.* `ACTION_REJECTED` carries two
different meanings — a command the controller refused, and a demand the envelope
protection eased back. The second is the safety layer working, and it fires on
every control tick an aggressive manoeuvre lasts. A clean waypoint turn at
t≈179 s produced 45 events in 1.5 seconds and scored the leaders zero on safety.
The term now measures rejections and interventions separately, and interventions
as a fraction of the run rather than as a count.

*Formation was averaged over the whole run.* A wingman spawns 1.7 km from
station and needs about 90 seconds to close. Measured across the run the mean
station error is 382 m and the score is zero; measured over the settled tail it
is 82 m with 100% of samples in station. Only the tail is scored, and the term
says so.

*Route progress was unreachable.* Crediting four waypoints in a scenario whose
first leg is 40 km — three minutes at cruise — meant no run could earn the term.
Progress is now judged against what the run had time for, and skipped entirely
when the run was too short for even one waypoint.

*A wingman was scored on route progress it was never given.* Wingmen hold
station; they have no waypoints. The term is now marked inapplicable for them.

### A test that had been failing since PHASE 8

Running the full e2e set for the first time since PHASE 8 showed `smoke.mjs` and
`simulation.mjs` both failing: making the 3D view the default stage removed
"Tactical Plot" from the initial screen, and neither test was updated. PHASE 8
was shipped having run only `test:e2e:3d`. Both now assert the 3D view is the
default and that the 2D plot is still reachable.

**Verified:** a recorded run and an unrecorded one produce identical state
hashes, and each recorded frame's hash matches what the engine held at that
tick. 5x playback advances at 5x. A run killed mid-write still loads. A path
outside the replay directory is refused. Deleting a run removes its rows and its
file. 362 backend tests, plus `npm run test:e2e:replay`, which records a run
through the UI and then plays, scrubs and scores it.

## PHASE 10 — Scenario editor — **Complete**

Scenarios can now be written from the dashboard, not just read. Create, edit,
clone, delete, import and export, with a live preview of where the units start.

**The preview reuses the tactical view.** Edit mode is a third stage source
beside live telemetry and replay, so placing an aircraft is something you can
see. `useStage()` is the single place that decides which of the three is on
screen; the 3D view, the 2D plot and the entity list all read it, and therefore
cannot disagree about what they are showing.

**Validation is the simulation's own parser.** The editor cannot accept a
scenario the engine would refuse, because there is only one validator. Errors
come back naming the unit and the problem — "entity BLUE-02: formation leader
'GHOST-99' is not declared in this scenario" — rather than "invalid".

### The bug that had to be fixed first

`Scenario.to_dict()` dropped `orientation`, `health`, `energy`, `fuel`,
`route_loop` and `formation_offset`. Harmless while nothing ever wrote a
scenario back; silent data loss the moment something did. A form that loaded
demo_alpha, changed the duration and saved would have flattened every wingman's
station offset and every aircraft's initial attitude.

So serialisation was made lossless before anything was built on it, and
`parse_scenario(s.to_document()) == s` is now a test.

### Three refusals

*A name is a filename.* `../escape`, `a/b`, `con` and names with spaces are
refused rather than sanitised into something the caller did not ask for.
Sanitising would silently write to a different file than the one requested;
refusing says so.

*An invalid scenario is never written*, so every file in the directory loads and
the list is trustworthy.

*The scenario a run is flying cannot be changed underneath it* — `409`, naming
the reason.

### Two things found by running it

*Saved scenarios were owner-only.* `NamedTemporaryFile` creates at `0600`, so
every file the editor wrote had different permissions from the ones shipped with
the project, and would have stopped loading the moment the backend ran as
another user — in a container, or after a `sudo`. The mode is now set before the
rename, and an existing file keeps whatever mode it had.

*The editor test grabbed the wrong input.* The name field was located by
position, and the first input on the page belongs to the import panel, so the
test filled that instead and then failed several steps later. The editor's
fields now carry accessible labels, which is what the test targets — and what a
screen reader needs.

**Verified:** a scenario built through the UI saves, validates, exports,
re-imports and then **actually flies** — `npm run test:e2e:editor` asserts the
engine reaches a positive tick count with the units the editor declared. 420
backend tests, including round-tripping, every unsafe name, a failed write
leaving no temporary file behind, and the in-use and protected refusals.

## PHASE 11–13 — Reinforcement learning — **Complete**

A flight policy can be trained against the simulation itself. Same 6DOF physics,
same sensor noise and dropout, same datalink, same safety layer between the
policy's output and the control surfaces. Nothing is bypassed for training.

**The policy is an ordinary agent.** `PolicyAgent` is a `BaseAgent` like any
other, so a network flies through the identical pipeline a rule agent does. It
sees the observation the sensor model produced — never truth — and its controls
are validated, envelope-protected and rate-limited on the way to the physics. A
policy that learns to fly here has learned to fly the aircraft, not to exploit a
shortcut.

**Nothing models weapons, engagement or targeting.** The task is flight and
navigation, and the reward has no term for anything else; a test asserts the
term names.

The engine needed no changes for any of this. The environment drives it through
`step()` and the existing agent registration, which is the payoff for the
one-way pipeline having been kept honest since PHASE 1.

### The defect that stopped it learning

The first trained policy scored **worse than an untrained one** — 704 against
782, reaching no waypoints. That is the kind of result that is easy to explain
away as "needs more timesteps". Reading the per-term breakdown showed it was not
that.

Of roughly 780 total reward: survival 400, coordination 200, information 85,
smoothness 78 — all nearly constant whatever the policy did. Navigation, the
only term it could influence, moved by about ±40. **Five percent signal**, and
PPO was fitting a value function dominated by a constant.

Two fixes, both in what was being measured rather than in the algorithm:

*The terminal crash penalty was split out of per-step survival.* One term was
doing two jobs — paying for each step alive and punishing the loss of the
aircraft. Rolled together they made a constant that paid every step regardless
of behaviour, while the thing it was supposed to deter was buried inside it.
Exactly the same shape of mistake as PHASE 9's clamp-versus-rejection defect.

*Terms a policy cannot influence became cheap, and ones it can became dear.*
Survival 1.0 → 0.05, mission 2.0 → 5.0, navigation left at 1.0.

Retrained on the same seed and budget: navigation 36.6 → **241.7**, and the
policy beat both the untrained network and a hand-trimmed constant baseline
(306.5 against 98.4 and 203.8), with no crashes.

A third calibration came from the same reading: `progress_scale_m` was a round
200 m while a step at cruise covers 22 m, so navigation could never exceed 0.11
against survival's 1.0. It is now derived from cruise speed times step duration.

### A scenario for the task

`training_navigation` was added because demo_alpha's first leg is 40 km — three
minutes at cruise — so a two-minute episode ended before the aircraft reached a
single waypoint and the navigation reward never fired. Its legs are about 5 km,
and a second unit flies its own circuit so the policy learns in traffic.

### Training is CLI-only, and the dashboard says so

`backend/train.py` runs PPO or SAC. The dashboard shows the device, the
environment, every reward term and every trained policy, and prints the command
— but has no START button. A long job needs progress reporting, cancellation and
survival across a page reload; a button that cannot do those things would be a
control that does not do what it appears to. That belongs to the training centre.

Every model saves a card naming the scenario, seed, hyperparameters, observation
layout version and reward weights. Loading a model whose layout version has
moved on logs a warning rather than silently feeding it inputs that no longer
mean what they meant.

### Two things found by running the whole suite

*Deleting a loaded scenario broke START.* The PHASE 10 delete guard covered the
scenario a run was *flying*, but not one merely loaded. Deleting it left the
engine pointing at a file that no longer existed, the dashboard went on offering
it, and the next START failed with "scenario file not found" for no visible
reason. The engine now releases it.

*Every Playwright click hung once the 3D scene was rendering.* Not a product
bug: with no GPU the scene is drawn by swiftshader on the CPU, which saturates
the browser's main thread, and Playwright's post-click settlement check starves.
Measured — with the canvas removed the same click completes in under 100 ms, and
a direct DOM click always worked. The 3D suite now asserts the button is visible
and enabled and dispatches the click, which loses none of the assertion.

**Verified:** `gymnasium.utils.env_checker.check_env` passes; the same seed
reproduces a rollout exactly; the policy cannot act faster than a rule agent;
commanding full deflection every step still leaves the applied controls inside
their bounds, proving the safety layer is still in the path. PPO and SAC both
train, save, reload and evaluate to the same numbers. 468 backend tests.

## PHASE 14–15 — Multi-agent and commander — **Complete**

Eight units, two teams, and a commander per team that decides *what each unit
should be doing* — and nothing else.

**A commander has no aircraft and no path to a control surface.** `CommanderAgent`
is not a `BaseAgent`: it produces `Task` objects and hands them to `TaskManager`,
which is the only thing that records who was told what. A unit's `RuleAgent` then
decides whether it can carry the task out. The flight controller remains the sole
writer of entity controls, exactly as it has been since PHASE 4, and a test
asserts a commander cannot reach one.

**A unit may refuse.** `apply_task()` returns a reason code either way —
`ROUTE_AVAILABLE`, `LEADER_AVAILABLE`, `NO_ROUTE`, `LEADER_UNKNOWN`. An order is
a request, not a write into the world; the Coordination panel shows the refusals
next to the acceptances, so a team that is not doing what it was told is visible
rather than silent.

**A commander only knows what the datalink told it.** `TeamManager` builds its
picture from `datalink.tracks_for()` and nothing else, so every position it
reasons about carries a `report_age_s` and is subject to the same latency, loss
and blackout as any other message. It cannot read truth. During a blackout the
picture simply ages, which is the correct behaviour and is what a test checks.

**Tasks are flight tasks.** `TaskType` is `PATROL`, `TRANSIT`, `ESCORT`, `HOLD`.
Nothing models weapons, engagement or targeting, and a test asserts the member
names.

### Routing, at last

`CommunicationManager` was added because the inbox had become a place where
different kinds of message were drained by whoever got there first. It drains
each unit's inbox exactly once per tick and routes by type — `POSITION_REPORT`
to the datalink, `TASK_ORDER` to the order queue, `STATUS` to the status queue —
and counts anything it does not recognise as `unrouted` rather than dropping it
quietly.

### Four defects worth recording

*The commander declared every leader lost at t=0.* Coverage is computed from
datalink tracks, and at the first tick no position report has been delivered yet
— latency alone guarantees that. Coverage was therefore 0.00, every leader
looked missing, and the commander promoted wingmen before the simulation had
produced a single message. Silence before the first report is not evidence of a
loss. The commander now trusts the plan until it has heard from the team at
least once:

```python
member = picture.member(leader_id)
if member is None or not member.heard_from:
    # Trust the plan until the team has actually been heard from once.
    return not self._heard_anything
```

*Eight tasks in one allocation pass got the same id.* `new_task_id()` was built
from a millisecond timestamp, and eight tasks issued inside one pass land in the
same millisecond — measured: **one unique id out of eight**. The `TaskManager`
records overwrote each other and the register showed BLUE-01 escorting itself.
A process-wide counter now makes the id unique regardless of clock resolution.
The clock part is kept only so ids sort roughly by age.

*A panel painted over the one below it.* Not a coordination bug, but found by
running the app to look at the new panel: a `Panel` sizes itself to its content,
so dropping one into a `flex-[3]` slot let it spill — with eight agents the
decision feed rendered **6080 px tall inside a slot that had collapsed to 0**,
over the training panel. The rails now scroll and floor their feeds, as the
telemetry rail already did.

The new layout check then found a second instance nobody had seen: in replay
mode at 1280×720 the taller transport panel left the tactical view 171 px, and
the view's own 320 px floor pushed it 149 px past the bottom of the window, over
the footer. `e2e/coordination.mjs` measures every panel against its slot at two
viewport sizes in all three stage modes, which is what caught it.

*The dashboard header said PHASE 9 after three phases had shipped.* A literal in
`health.py` that nobody remembered to bump. Bumping it again would only have
reset the clock on the same mistake, so it is now checked against the last phase
marked **Complete** in this file.

### Scaling, measured

3000 ticks per point, after a 200-tick warm-up, on this container:

| agents | µs / tick | ticks / s | × real time | cost vs 1 agent |
|-------:|----------:|----------:|-------------:|----------------:|
| 1 | 254 | 3934 | 65.6× | 1.00× |
| 2 | 500 | 2000 | 33.3× | 1.97× |
| 4 | 1023 | 977 | 16.3× | 4.03× |
| 8 | 2080 | 481 | 8.0× | 8.18× |

Linear in the agent count, and eight agents still run at eight times real time.
The agent loop is not what will limit this.

The datalink is. Transmissions grow linearly — one report per unit per cycle —
but each transmission is copied into every teammate's inbox, so the copies grow
with the *team* size:

| agents | transmissions / cycle | inbox copies / cycle |
|-------:|----------------------:|---------------------:|
| 1 | 1.0 | 1.0 |
| 2 | 1.9 | 3.9 |
| 4 | 3.9 | 15.4 |
| 8 | 7.8 | 30.8 |

(Eight units in two teams of four, so each report reaches four recipients, not
seven — which is why the last row is not four times the one above it.) A single
team of 32 would be the first thing to hurt, and the fix when it does is a
per-team fan-out rather than a per-recipient copy.

**Verified:** recording eight agents leaves `state_hash` identical run to run;
a leader going DISABLED is followed within 2 s by the commander moving BLUE-02
onto the leader's four-waypoint route while RED-02 keeps escorting, confirmed
against the agent's own state and not just the register; a commander holds no
reference that can reach a control surface. 497 backend tests.

## PHASE 16 — JSBSim adapter — **Complete**

PHASE 1 declared an `Integrator` protocol so the physics could be swapped. This
is the phase that proves it was worth declaring: JSBSim, an established
open-source flight dynamics model, now flies the world through that protocol and
the engine needed no change to let it.

Until now the swap was reachable only by passing an instance to the engine's
constructor — which is to say, from tests and nowhere else. `physics.backend` in
`configs/simulation.yaml` now selects between `simple_6dof`, `jsbsim`,
`kinematic` and `null`, and `/api/physics` and the Command Center report which
one is actually flying.

### The platform works without it

JSBSim is optional, and that is not a claim — it was **verified by uninstalling
it and running the whole suite**: 528 passed, 9 skipped. The app starts, the
dashboard marks the backend `NOT INSTALLED` with the command that installs it,
and the default backend goes on flying.

Asking for JSBSim when it is absent raises an error naming the package. It does
**not** fall back to `simple_6dof`. A run recorded under a model nobody chose is
worse than a run that refused to start, and `config_hash` — which is what makes
a run reproducible — would then describe physics the run never used.

### No real aircraft, ever

JSBSim ships definitions for real airframes, which would break this platform's
first rule on the first tick. So the adapter never touches them: it points
JSBSim at a data root it generates itself, holding only the fictional airframes
`simulation/aircraft.py` already describes.

Generating rather than hand-writing the XML is what keeps the two backends
honest about each other. Both read the *same* `AircraftParameters` — mass,
inertia, reference geometry, every aerodynamic coefficient — so a difference
between them is a difference between the **models**, never between two airframes
that drifted apart in separate files. A digest beside each generated file means
editing a parameter regenerates it rather than leaving a stale airframe flying a
platform the rest of the system has stopped describing.

Two deliberate choices in that generated airframe. **Thrust is an external force,
not an engine**: JSBSim's engine types each carry their own spool-up and altitude
lapse, and our fictional platform's model is `max_thrust_n * throttle` along body
X, which an external reaction reproduces exactly. **Lift and drag are tables**,
because `Cl` saturates at `cl_max` to model stall and JSBSim has no clamp inside
a product. A test asserts the airframe describes no weapon, store, pylon or
targeting element — checking element and property names, not raw text, because
the file's own header comment contains the word "weapon" in saying there is none.

### Three boundaries

**Round earth to flat plane.** JSBSim positions a vehicle geodetically; the world
state is a local tangent plane in metres. The adapter converts through the WGS84
radii of curvature at a reference latitude. Measured, the round trip is exact to
better than a micrometre across the entire ±100 km world box.

**Its atmosphere, not ours.** JSBSim models a standard atmosphere; `simple_6dof`
uses the constant from config. At 6 km that is 0.66 against 1.225 kg/m3, so the
same airframe at the same throttle does not hold level flight under both. Left
visible rather than papered over: the agents fly closed-loop and trim themselves,
and on `demo_alpha` the route followers settle back onto their assigned altitudes
within about 100 s and hold to within a couple of metres. A test asserts that,
so if it ever stopped being true it would show as a steady descent rather than as
nothing.

**Truth stays authoritative.** JSBSim is the one component with a private copy of
where the aircraft is, which makes it the one that could quietly become the thing
the world follows. So the adapter records exactly what it last wrote; when the
truth state no longer matches — a reset, an out-of-bounds clamp, a scenario
reload — JSBSim is re-initialised from the world rather than the other way round.

### The defect the phase found in itself

*A whole configuration section did nothing.* `physics:` was added to
`simulation.yaml`, the `Settings` field was declared, the validator worked, the
tests passed — and editing the file changed nothing, because `load_settings`
builds `Settings` field by field and the new one was never passed. The field's
default silently took over and the API cheerfully reported it. Found by running
the real app and seeing it report `simple_6dof` with `jsbsim` in the file.

The fix is one line. The interesting part is the guard: every top-level section
in every shipped config file is now checked against what `load_settings`
actually reads. It immediately found a **second** instance — `rl_agent:` in
`agents.yaml`, a PHASE 0 placeholder marked "Populated in PHASE 11+" whose
promise was never kept. PHASE 11-13 loads a policy by the training run that
produced it; setting `model_path` there did nothing at all. Dead configuration
reads exactly like live configuration, so it was deleted.

### Measured

Four aircraft, 3000 ticks after a 300-tick warm-up:

| backend | µs / tick | ticks / s | × real time |
|---|---:|---:|---:|
| `simple_6dof` | 1024 | 976 | 16.3× |
| `jsbsim` | 643 | 1509 | 25.1× |

JSBSim is the **faster** of the two, at about 0.63× the cost, which was not the
expected result. Compiled C++ beats four NumPy derivative evaluations per RK4
step in the interpreter. The optional, more detailed model is not the slow one.

**Verified:** the same seed reproduces a JSBSim run exactly; the two backends
produce different state hashes on the same scenario, as two different models
should; commanding full deflection every tick still leaves the applied controls
inside their bounds, so the safety layer is still in the path; teleporting an
aircraft mid-run leaves JSBSim following the world rather than overwriting it.
537 backend tests, 9 of which skip when JSBSim is not installed.

## PHASE 17 — Analytics — **Complete**

Charts of a finished run: altitude and speed per unit, how many of each team
were still flying, how many were coordinating, where every unit's score came
from, and what each one spent its decisions doing. Plus a side-by-side
comparison of several runs.

A fourth stage mode, **ANALYTICS**, sits beside LIVE, REPLAY and EDIT. It is the
only one with no tactical view, because it is about *runs* rather than about a
run.

### Aggregation belongs in the backend

A run holds thousands of telemetry samples and up to five thousand stored
decisions. Shipping those to a dashboard so it could group them would make every
chart a download, and two clients grouping the same rows differently would
disagree about the same run. So `analytics/series.py` answers in the shape a
chart consumes — named series of points, one matrix, one set of shares — and the
browser only draws.

The layer takes rows and returns numbers. It cannot reach a tick, and a test
asserts it does not even import the engine, which is what makes it safe to add a
chart without any risk of changing how an aircraft flies. Analysis is downstream
of truth in exactly the sense scoring has been since PHASE 9.

### A run with nothing stored says so

Recording can be switched off, and a run shorter than a second was never
sampled. Returning empty series would draw a flat line at zero — which is a
*claim about the run*, not an absence of data. Every chart carries whether it
has anything and why not, and the panel prints the reason where the chart would
have been.

The first capture of the new stage landed on exactly this case: it opened the
newest run, which was one tick long, and showed three charts explaining their
own emptiness. Honest, but a poor front door. It now opens the newest run that
flew for at least `tick_rate_hz` ticks, which is the shortest run that can have
been sampled at all.

### What the colour work turned up

The palette was not chosen by eye. Each set was put through the dataviz
validator against the panel surface — lightness band, chroma floor,
colour-vision separation, normal-vision separation, contrast — and two results
changed the charts themselves:

**No six-hue categorical set survives an all-pairs check.** Blue and violet come
out ΔE 0.3 apart under deuteranopia however they are stepped, and constraining
the search to avoid the two team hues returned nothing at all. So the six
scoring terms are **not** six colours: they are a heatmap on one hue, light to
dark. That also suits the question better — *where did this unit lose points* is
magnitude, not identity.

**Eight units cannot each have a hue either.** Unit lines are coloured by team,
two hues that are safe on every pair (worst ΔE 24.1 under protanopia), and
identity rides a direct label at the end of each line.

The five behaviour colours were validated *in their stacking order*, because the
check is on adjacent pairs — so the backend emits behaviours in that order and a
test pins it. Reordering the stack would quietly void the separation the palette
was picked for.

Chart marks also got their own steps rather than reusing the HUD tokens: a 2px
line is dense in a way a badge is not, and the bright neon tokens sit above the
lightness band for dark surfaces. Same hue families, so a team keeps its colour
everywhere.

### Two drawing defects, both found by looking at it

*End labels collided into unreadable overprints* — the speed chart rendered
`BEDE001` where `BLUE-01` and `RED-01` had been drawn on top of each other. The
labels were sorted by data value while the nudge that separates them pushes down
the screen, and the y axis is inverted, so the sort walked one way while the
nudge walked the other. Sorting by screen position fixed it.

*A single-sample series drew nothing at all.* A `polyline` through one point has
no length. One sample is a real measurement, so it gets a marker instead.

**Verified:** four charts drawn from a real recorded run, ten lines, a heatmap
cell for every unit against every term, the crosshair reading a real time off
the series, a two-run comparison, and a run too short to be sampled reported as
unavailable rather than charted as zero. 551 backend tests.

## PHASE 18 — Training centre — **Complete**

`train.py` wrote the specification for this phase in PHASE 13, in the docstring
explaining why there was no button:

> Training is CLI-only for now. Driving a long job from the browser needs job
> management — progress, cancellation, surviving a reload — which is the
> training centre's work in a later phase. Rather than put a button in the
> dashboard that cannot do those things, the dashboard says training is run
> from here.

All three exist now, so the button does too. A fifth stage, **TRAINING**, starts
a real job, follows it, and stops it.

**Progress** comes from an SB3 callback reporting timesteps and the running mean
episode reward. It is sampled rather than recorded per step, and the series is
thinned when it outgrows its budget, so a long job keeps a readable curve
instead of a million points.

**Cancellation** returns False from that callback, which ends `learn` at the
next step boundary. Nothing is killed mid-update: the policy trained so far is
saved and usable, and the run is recorded as CANCELLED rather than quietly filed
as a completed one that happens to be short. Measured: stopped at 1,429 of
200,000 steps with the model on disk.

**Surviving a reload** falls out of the job living in the server rather than the
page — the e2e test reloads mid-job and rejoins it. Surviving a *server restart*
is a different claim and is **not** made: a job cannot outlive its process, so
every run left RUNNING by a restart is reconciled to INTERRUPTED at startup
rather than left looking like it is still going.

### What it refuses, and why it says so

A refusal that does not explain itself is worse than no button. Each one names
its reason, and the dashboard prints what the backend said:

* **A simulation is running.** Both would contend for the same cores and
  neither's timings would mean anything. Stop the simulation first.
* **A job is already running.** One at a time. Queueing silently would leave the
  operator watching a progress bar belonging to somebody else's job.
* **Above the cap.** `max_timesteps_per_job` in `configs/training.yaml`, 500,000
  by default. A browser request should not be able to commit the server to a
  week of compute; the command line has no cap, which is where a long run
  belongs.

That last one needed a change in the API client too. `ApiError` was throwing
away the backend's `detail` and reporting only a status code, so every one of
these carefully-worded refusals would have reached the operator as "POST
/api/training/start failed (400)".

### Three defects, one of them caught by its own test

*A request for zero timesteps started a 200,000-step job.* `timesteps or
configured_default` treats an explicit zero as an omission. An expected-refusal
test failed by not raising, and left a real 200k job running behind it. Zero is
a mistake to refuse, not a gap to fill in — `is None` now.

The same line had a second bug beside it: the default budget was always PPO's,
whatever algorithm was asked for.

*The metric budget was computed from the wrong number.* The sampling interval
came from the requested timesteps, but PPO collects in whole blocks and
overshoots — a 600-step request ran 2,048 steps and produced 683 points against
a 200-point target. The curve is now thinned when it exceeds its cap, halving
the resolution rather than dropping the newest progress.

*The training clock started before the environment existed.* The callback's
stopwatch began when it was constructed, which is before `train()` builds the
env, so the first sample carried about 15 seconds of setup as if it were
training time. It starts at `_on_training_start` now.

### What is still true and still said

A job runs **in this server process**. That is stated on the panel, in the API
and in the job status, because it is the thing that makes the feature honest:
one job at a time, no restart survival, and a long run belongs on the command
line where it can outlive the dashboard.

**Verified:** a real PPO policy trained from the browser — 4,096 steps, 274
progress samples, a reward curve climbing from -82 to -58 — followed across a
page reload, refused on top of a running simulation with the reason on screen,
and cancelled part way with the partial policy saved. 570 backend tests.

## PHASE 19 — Model centre — **Complete**

A `.zip` on disk is not a usable policy. It is weights that expect a particular
observation vector, shaped by a particular reward. Run one against a different
one and **nothing fails**: it loads, it flies, it returns a score, and the score
looks exactly like a real measurement. That silent wrongness is what this phase
is built around, which is why most of it is a refusal.

Every saved policy now leads with a verdict rather than a size and a date:

| Verdict | Meaning |
|---|---|
| `COMPATIBLE` | Same observation layout, same reward. It means what it meant. |
| `DIFFERENT_REWARD` | Same layout, so it runs — but it was optimising something else, so its score is real and *not comparable*. |
| `INCOMPATIBLE` | The layout has moved on. Its inputs no longer line up, so **evaluation is refused**. |
| `UNKNOWN` | No card, or one that cannot be read. Nothing can be said about it, which is itself worth saying. |

`DIFFERENT_REWARD` is deliberately still runnable. Measuring a policy shaped by
another reward is exactly how you find out what that reward produced; what must
not happen is quietly ranking it against one that was optimising something else.

### Evaluation is a job, because it is slow

Measured before building anything: three episodes took **76 seconds**. Far too
long to hold an HTTP request open, so evaluation goes through the same runner
training uses — which also settles the question of what happens when both are
asked for at once. One job at a time now spans *both kinds*, because they
saturate the same cores and running one of each would make both of their
numbers meaningless.

Cancellation lands between episodes rather than inside one: an episode is the
smallest unit that can be stopped without reporting a partial one as if it had
finished. A cancelled evaluation says how many episodes actually ran, and
**does not write itself onto the card** — a mean over two of five episodes is a
different measurement, and filing it as the policy's score would misrepresent it
every time it was read afterwards.

A complete one does go on the card, because a measurement that lived only in a
job's memory would be gone at the next restart.

### Archiving is not deleting

A model that stops being interesting is usually not a model that should be
destroyed, and an experiment that is no longer in the list is still evidence.
Archiving moves the policy **and its card** into a subdirectory; a policy
without its card is one nobody can judge. Delete is there too, and says what it
does.

### Two things the tests were written to stop

*A model id is a path.* Ids arrive from the API, so `../` in one must not be
able to read or unlink a file elsewhere. `path_for` refuses any id containing a
separator or starting with a dot and checks the resolved parent, and a
parametrised test walks the obvious attempts.

*A comparison that quietly means nothing.* Lining up two policies is only
meaningful when they were shaped alike and both have been measured, so the
comparison states which of those conditions fail — different layouts, different
rewards, different scenarios, or simply not evaluated yet. Same discipline as
PHASE 17's `weights_hash`: numbers from different rulers do not go in one table
without a warning.

### What running it turned up

The model list did not refresh when an evaluation finished. The score was
written to the card correctly, the job showed COMPLETED in the panel above, and
the list below still said "not evaluated" until the page was reloaded — asking
the operator to reload to see the number they had just asked for. It now watches
the job and refreshes itself when one ends.

**Verified:** a real policy measured from the browser with the score persisted
to its card; a policy with a stale observation layout marked INCOMPATIBLE, its
evaluate button disabled, and its evaluation refused with 409; archive and
restore round-tripping with the card following the policy. 599 backend tests.

## PHASE 20 — Production hardening — **Complete**

The last phase, and mostly a matter of going back over nineteen others rather
than adding a twentieth feature.

### One command

`aifcs` replaces "which of these four scripts do I want":

```
aifcs doctor                    check the installation and say what is wrong
aifcs serve                     run the backend
aifcs run demo_alpha -s 60      fly a scenario headless and report it
aifcs scenarios                 what can be flown
aifcs train --timesteps 20000   train a policy
aifcs models                    saved policies and their verdicts
aifcs evaluate MODEL            measure one
aifcs bench                     how fast the simulation runs here
```

`doctor` is the one that earns its place. A beginner whose dashboard will not
load needs to know *which* part is wrong, and it checks Python, every
dependency, the optional ones, whether the configs load, whether every scenario
parses, whether the database opens and whether the frontend was installed — in
about two seconds, with a line per check. A test drives it against a deliberately
broken scenario directory, because a diagnostic that crashes on the broken
installation it exists to diagnose is worse than none.

### An error you can do something with

An unhandled failure used to return the string `Internal Server Error`. Nothing
leaked, which was the important part, but the dashboard — which reads `detail`
from every deliberate refusal in this API — had nothing to show, and the
traceback in the log could only be matched to the response by guessing at
timestamps.

Now every response carries `X-AIFCS-Request-Id`, every error body repeats it,
and a 500 names it in a sentence. The exception text is still **not** returned:
a message can carry a path, a configuration value or part of a payload, and the
person holding a request id is not necessarily the person who should see those.
The id is the handle; the log has the detail. Deliberate refusals keep their own
words, and a malformed body now gets a 422 naming the field.

### The security review

Run over the whole branch, and it found something real.

Several endpoints build a filename from an identifier that arrives in a request.
Model ids were validated (PHASE 19) and scenario names were (PHASE 10), but
**run ids were not**. `POST /api/replay/load` takes one in the *body*, where a
path separator survives intact, and turned it straight into
`data/replay/<run_id>.jsonl.gz` — an arbitrary read of any `.jsonl` file the
process could reach. `DELETE /api/runs/{run_id}` did the same and then called
`unlink()`.

A run id is minted by the platform as `YYYYMMDD-HHMMSS-xxxx`; anything else is
not one, and is refused by name. `test_path_safety.py` holds every identifier
that becomes a filename to that rule, and ends with a structural check: a module
under `api/` that joins a request value onto a directory must also name a
validator, so the next such endpoint is caught in review rather than in a later
security pass.

The rest of the review came back clean, and is recorded here so the next person
does not have to redo it: every YAML load is `safe_load`; the one SQL string
built by formatting is `PRAGMA user_version = {int(version)}`, which cannot take
a bound parameter and is coerced; no `subprocess`, `eval` or `exec` anywhere; no
credentials in the repository; every numeric `limit` has a ceiling.

Two findings that are properties rather than bugs, now written down in
`docs/DEPLOYMENT.md` instead of being implicit: **there is no authentication at
all**, which is the right call for a tool on a researcher's laptop and the wrong
one the moment the port is reachable by anyone else; and **model files are
pickles**, so anything dropped into `models/` executes on evaluation — no
endpoint accepts an upload, but that directory is trusted input.

### The Docker build, finally settled

It has been carried as "unverified" since PHASE 0. The reason turned out not to
be the registry: **this environment has the Docker client and no daemon**, so
the images cannot be built here at all, and no amount of retrying changes that.

What could be done was to check the class of mistake a build would catch, which
found a real one: the backend image copied `backend/` and `configs/` but never
`scenarios/`. Compose bind-mounts the scenarios in, so the stack worked and
`docker run` of the image alone could not load a scenario. `test_docker.py` now
checks that every `COPY` source exists, that the image carries everything it
needs to start without a bind mount, that compose points at real Dockerfiles and
existing mounts, that nginx proxies both `/api/` and `/ws/`, and that the
healthcheck probes an endpoint that exists.

The images still have not been *built*. That is stated in the deployment page
rather than quietly dropped.

### Measured, on this machine

```
aifcs bench
scenario               units   us/tick   ticks/s  x real time
demo_alpha                 4     933.4      1071        17.9x
team_eight                 8    1990.5       502         8.4x
```

**Verified:** `aifcs doctor` reports a healthy installation and reports a broken
scenario directory as broken rather than crashing on it; the CLI produces the
same `state_hash` twice from the same seed, so it is not a second,
differently-seeded way to fly; an unhandled failure returns a request id and not
the exception text; every traversing identifier is refused. 655 backend tests,
nine e2e suites.

---

## Where this leaves the platform

Twenty phases, and the through-line held: **the AI never writes the truth
state**. Every layer added since — the safety layer, the commander, the learned
policy, the second physics backend, the analytics, the model centre — sits on
one side or the other of that line, and each phase's tests say which.

The three rules that did the most work, in the order they paid off:

**A one-way pipeline makes things cheap to add.** Scoring, analytics and the
model centre are all downstream of truth and cannot reach a tick, so a new chart
or a new measurement carries no risk of changing how an aircraft flies. Three
separate phases were fast because of a decision made in PHASE 1.

**An honest empty state is worth more than a plausible one.** A run with no
samples says so rather than drawing a flat line at zero; a backend that is not
installed says so rather than falling back; a policy whose observation layout
has moved on is refused rather than scored. Every one of those started as a
temptation to show something reasonable.

**Run it and look at it.** The layout defect that painted one panel over
another, the stage that opened on an empty run, the model list that would not
refresh, the header that overflowed on a phone — none of those were caught by a
test suite that was passing. They were caught by opening the thing.

**Optional has to be tested as absent.** After PHASE 20 the backend was
installed on a Windows desktop with the core requirements only. It would not
start: `training/pipeline.py` imported the Gymnasium environment at module
scope, the runtime imports the pipeline, so `backend.main` pulled an optional
dependency in on every boot. The suite said nothing, because every RL test
skips itself when the stack is missing — the skip condition and the breakage
were the same condition. `aifcs doctor` said "Ready" a second before the
traceback, because it imported the pieces and never the application.

Both of those are now checked: `test_optional_dependencies.py` starts the API
and runs the simulation in a subprocess with the optional packages made
unimportable, and doctor's last check before the frontend is whether the
application imports at all.

## The competition layer (COMP PHASE 1-3)

The 2026 AI 飛行員擂台賽 (NCSIST-AIPilot) fixes the interface and the scoring,
so the work here is faithfulness rather than design: 26 doubles in, four control
channels out, 60 Hz, UDP, and a judge on the other end who is not running our
code. Three things were built, and each of them found something.

**One implementation, not two.** The reference package encodes the state twice —
in the training environment and in the competition client — and the copies have
drifted: a high-speed elevator limit in one and not the other, two different
definitions of the speed it triggers on, a float32 cast on one side that adds
+/-12.7 m/s of noise to a closure rate the other computes exactly, and per-round
state held in module globals that no round boundary resets. All four exist only
because there are two copies. The parity tests vendor the organiser's own two
functions and use them as an oracle, so "faithful" is a thing that fails a test
rather than a thing that is claimed.

**The round boundary is a signal, not a message.** Nothing in the ICD says a
round started. What the host does is repeat the initial position unchanged
between INIT and START, so a held position after movement is the boundary — and
it takes exactly one frame to recognise, because a position is not held until it
has been seen twice.

**The scoring is not the reward.** `_compute_reward_and_done` pays for a nose on
target at any range; the competition pays between 500 and 3000 ft and pays more
for accumulated seconds than for any instant. Implementing the published scoring
turned that from an argument into a measurement — and the first thing it
measured was the reference policy itself, over five-minute rounds against the
non-manoeuvring opponent it was trained on: killed twice in three rounds, at 46 s
and at 180 s, and in the third round never got inside the envelope at all.

That is the baseline. It is beatable, and now there is a ruler that says by how
much.

### What the host actually said (COMP PHASE 6)

Three questions had been carried since PHASE 4 with no way to settle them from
any document. The probe asked the host and it answered all three in one session
of two rounds.

**A round starts at 340 knots.** 339.9 KCAS, 444.5 KTAS, Mach 0.673 at 19,116 ft.
The host does not have the initial-condition ordering defect, so the published
figure is the real one and the default here — altitude before speed — was right.
The consequence runs the other way: the reference package trained its 314M-step
policy at Mach 0.47 for a competition that runs at Mach 0.67, which is its
mismatch and not ours.

**The round boundary is a held position.** 6,729 frames repeated the previous
position exactly, and both rounds were detected from that pattern alone. PHASE 2
inferred that mechanism from a note in the rules; the host does it.

**There is time to think.** 0.20 ms mean and 1.11 ms worst decision latency on
the competition laptop, against a 16.67 ms frame — 1.2% and 6.7%. No malformed
packets and no non-finite values in 33,090 frames.

One thing did not match and is open: the initial separation measured 3,604 ft
where the rules give 3,000 / 6,000 / 9,000. The probe measures a 3D range and
the rules may mean a horizontal one, which the recorded frames will settle.

**And one thing the host said that the rules do not.** The probe's two rounds
started 3,295.0 and 4,850.2 ft apart, on headings of 340 and 55 degrees — whole
feet and whole degrees, which is `randint(0, 12000) * 0.3048` and
`randint(0, 359)`, the reference generator, not the published
3,000 / 6,000 / 9,000. The readme calls that build 民眾公告版, a public release
for testing a connection, so a test host randomising is unremarkable; what it
means is that it cannot be used to check the round setup. The published figures
stay the default because they are the only statement about competition day that
exists, and `RoundSetup.measured()` records what was seen so the difference is
written down rather than remembered.

Reading that frame also found a defect in the probe's own analysis: its
separation left the cosine of the latitude out of the longitude conversion, a
10% error on the east component at 25 degrees north, which turned 3,295 ft into
a reported 3,604 and made the rules look wrong instead of the arithmetic. The
state encoder always had the cosine. This was the one place that grew a second
copy of it — the exact mistake the package is arranged to avoid, made in the
tool built to check the arrangement.

### Training that survives the machine being turned off (COMP PHASE 7)

Asked for: training that continues after the computer is shut down. That is not
a thing — no process runs on a powered-off machine. What was actually wanted is
that shutting down costs nothing, and that is a matter of writing enough to disk
often enough.

A session is a directory holding the policy, its optimiser, SAC's replay buffer
and a state file saying how many steps have been taken and what they were taken
against. `state.json` is written after the model, never before: a crash between
the two loses one checkpoint interval and leaves a consistent pair, where the
other order would leave a state file claiming steps the model does not have.

`--timesteps` is a total rather than an increment, because "train until it has
had five million steps" is the sentence someone means and it is the one that
survives being run twice. Resuming with a changed reward or environment is
refused and the difference named — steps spent on two problems make a card that
can only be right about one of them.

Two things found by running it. Handing Stable-Baselines3 a `tensorboard_log`
when tensorboard is absent raises ImportError out of `learn()` — not at
construction and not as a warning — so an optional way of drawing graphs killed
an overnight run outright. And a Windows laptop suspends, which loses nothing
but stops training, so the run asks the system to stay awake while letting the
display sleep.

The double-click launchers needed two things that are invisible when they work:
CRLF line endings, and `chcp 65001`, without which UTF-8 messages print as
mojibake under the codepage a Traditional Chinese machine uses. The shortcut
installer asks Windows where the Desktop is rather than assuming
`%USERPROFILE%\Desktop`, because OneDrive redirects it — and this machine's
nearly was.

### A launcher that says what it saw (COMP PHASE 8)

Moving to the laptop, `start.bat` printed two things that cannot both be true:

```
VITE v6.4.3 ready in 2794 ms
!! Dashboard did not start in 30s.
```

My first two explanations — IPv6 loopback, then the dev server binding only to
`127.0.0.1` — were guesses, and both were wrong: `vite.config.ts` already has
`host: true`. What settled it was the user running `npm run dev` by hand:

```
X [ERROR] Cannot read file "node_modules/@react-three/drei/core/TrailTexture.js":
系統資源不足，無法完成要求的服務。
Error: Build failed with 1 error
```

Windows' ERROR_NO_SYSTEM_RESOURCES (1450): handles or paged pool exhausted.
Vite prints "ready" when the server is listening and *then* pre-bundles
dependencies, so the log was honest and so was the timeout. The 3D view uses
two components from `@react-three/drei` and was importing them through the
package's barrel, which reaches all 320 of its files to find them. Naming the
two modules directly took the pre-bundle from 28.0 MB to 18.3 MB, −35%.

That reduces the pressure; it does not remove the limit, and saying otherwise
would be a fix that is really a hope. The Windows-side remedies — a Defender
exclusion for the repository, a reboot, closing memory-heavy applications — are
written down in `docs/COMPETITION.md` next to the change, because the change
alone may not be enough on a machine that is already close to the ceiling.

The launcher itself was the second defect. A message that reports only a
timeout, sitting under a log that says the server is ready, gives its reader
nothing to do. Both launchers now try `127.0.0.1` and `localhost` alternately
rather than exhausting one before the other, and on failure print each URL's
curl exit and HTTP code plus `netstat` for the port. The wording changed from
"did not start" to "did not answer", which is the thing actually observed.

### The other reason it would not start (COMP PHASE 9)

Running the launcher here, cold, produced a failure I had not seen before:

```
!!  Backend did not become healthy in 30s.      (first start)
    Backend ready — http://127.0.0.1:8080/docs  (next start, 2s)
```

Same code, same machine, back to back. The startup path imports torch so the
training subsystem can report whether it is available, and a CUDA build's
first import reads hundreds of megabytes of libraries. Cold page cache — or,
on Windows, antivirus reading each one — and thirty seconds is not close.

The backend was never broken. The deadline was, and the message it produced
blamed the wrong thing. Both launchers now wait 180 s, and neither goes quiet
while waiting: past 20 s they say which slow thing they are waiting for, then
account for the time every 30 s. Silence for three minutes is indistinguishable
from a hang, so raising the deadline alone would have traded one bad experience
for another.

Two things worth recording about the tests. The first draft of the deadline
test matched any number in the file and so read the port numbers as deadlines
— it would have passed on 8080 while the real deadline was 30. It is now
anchored on the variable name. And both tests were checked by breaking what
they guard and confirming they fail, which is the only way to know a test is
load-bearing.

Verified in the same session: the platform starts and every subsystem reports
ONLINE, the dashboard serves, training runs (3,000 steps), and resuming works
— a second run took the same session from 3,000 to 5,000 steps with the replay
buffer restored from disk.

### The dashboard stopped needing a dev server (COMP PHASE 10)

With the backend deadline fixed, the laptop got further and then stopped:

```
Backend ready - http://127.0.0.1:8080/docs
==> Starting dashboard / 啟動儀表板…
    ...still waiting (149s of 180s)
    Listening on 5173:
!!  Dashboard did not answer in 180s.
```

Nothing was listening. Vite never reached the point of binding the port, and
this time it produced no output at all — which the failure did not say, because
`Show-Log` returned quietly when a log was missing or empty. That is the same
defect as the timeout with no evidence, in the one place it had not been fixed:
"the process wrote nothing in three minutes" is the most useful line in such a
report, not the absence of one. Both launchers now always print the section.

The larger point is that the dev server was never the right tool here. It
exists for hot reload while editing the frontend, and starting the platform is
not editing it. The frontend already addresses the API with relative paths, so
the backend can serve the built bundle from the same origin with no proxy at
all: one process instead of two, no Node at run time, and none of the
dependency pre-bundling that exhausted the machine's file handles.

`built` is now the default and `dev` is a flag. The launcher builds the bundle
when it is missing or older than the sources, and skips the build otherwise —
measured here at 6.8 s to build and 3 s to start once built. Node is required
only when something has to be built, so a machine with a bundle and no Node can
still run the platform.

Honesty about what this does not fix: `vite build` also reads a great many
files and could hit the same Windows limit. It is Rollup rather than esbuild,
which opens far fewer at once, and it only has to succeed once — but that is a
better chance, not a guarantee, and the documentation says so.

The security-relevant part is the single-page catch-all, which is one wildcard
away from swallowing the API. Tests cover both, and the traversal test needed
two corrections before it was worth anything: the first version put its bait
file two directories above the bundle and probed a path that climbed one, so it
passed with the containment check deleted. It now includes the percent-encoded
forms that reach the handler with `..` intact, and a symlink whose name is
inside the bundle and whose target is not. With the check removed, two cases
fail.

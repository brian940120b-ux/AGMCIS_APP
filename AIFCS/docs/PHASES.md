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

## PHASE 16 — JSBSim adapter

JSBSim as one swappable physics backend behind the `AircraftModel` interface. The
platform must keep working without it.

## PHASE 17–19 — Analytics, training centre, model centre

Reward/score/survival/coordination charts, model comparison, live training metrics,
model lifecycle (load, unload, evaluate, compare, archive).

## PHASE 20 — Production hardening

Performance profiling, error handling, plugin architecture polish, CLI (`aifcs`),
deployment documentation.

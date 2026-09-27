def test_a_worker_is_told_to_use_one_thread():
    """Eight workers each starting an OpenMP pool sized to the machine is what
    kept killing v5 at spawn:

        OMP: Error #137: Cannot create thread.

    A worker runs JSBSim, which is scalar, and a small numpy forward pass.
    It has no use for a thread pool, and the variables only take effect if
    they are set before OpenMP initialises.
    """
    import os

    from competition.gym_env import _single_threaded

    saved = {k: os.environ.get(k) for k in ("OMP_NUM_THREADS", "MKL_NUM_THREADS")}
    for key in saved:
        os.environ.pop(key, None)
    try:
        _single_threaded()
        assert os.environ["OMP_NUM_THREADS"] == "1"
        assert os.environ["MKL_NUM_THREADS"] == "1"
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_it_does_not_override_a_thread_count_someone_chose():
    """`setdefault`: a person who set OMP_NUM_THREADS themselves outranks this."""
    import os

    from competition.gym_env import _single_threaded

    saved = os.environ.get("OMP_NUM_THREADS")
    os.environ["OMP_NUM_THREADS"] = "4"
    try:
        _single_threaded()
        assert os.environ["OMP_NUM_THREADS"] == "4"
    finally:
        if saved is None:
            os.environ.pop("OMP_NUM_THREADS", None)
        else:
            os.environ["OMP_NUM_THREADS"] = saved


def test_the_thread_limit_is_in_place_while_workers_spawn_and_gone_after():
    """Where the limit has to be said, and why it cannot be said in the worker.

    A spawned worker unpickles the builder, whose closure names `Monitor`, so
    Stable-Baselines3 — and torch, and OpenMP — are imported before any line of
    ours runs there. OpenMP reads the variable once, at init. The only moment
    left is the parent's environment at spawn time, which children inherit.

    And it has to be given back: the parent runs the gradient steps.
    """
    import os

    from competition.gym_env import _spawning_single_threaded

    saved = os.environ.get("OMP_NUM_THREADS")
    os.environ.pop("OMP_NUM_THREADS", None)
    try:
        with _spawning_single_threaded():
            assert os.environ["OMP_NUM_THREADS"] == "1", "children inherit this"
        assert "OMP_NUM_THREADS" not in os.environ, "and the parent gets it back"
    finally:
        if saved is not None:
            os.environ["OMP_NUM_THREADS"] = saved


def test_spawning_leaves_a_thread_count_someone_chose_alone():
    import os

    from competition.gym_env import _spawning_single_threaded

    saved = os.environ.get("OMP_NUM_THREADS")
    os.environ["OMP_NUM_THREADS"] = "6"
    try:
        with _spawning_single_threaded():
            assert os.environ["OMP_NUM_THREADS"] == "6"
        assert os.environ["OMP_NUM_THREADS"] == "6"
    finally:
        if saved is None:
            os.environ.pop("OMP_NUM_THREADS", None)
        else:
            os.environ["OMP_NUM_THREADS"] = saved


def test_make_vec_env_holds_the_limit_while_the_workers_are_actually_created(monkeypatch):
    """The wiring, which the context manager's own test does not give you.

    Unwrapping the spawn left every other test in this file green — the fourth
    time in this codebase that a correct helper was covered and its call site
    was not. What matters is the value of the variable at the moment
    SubprocVecEnv forks the children, so that is what this reads.
    """
    import os

    import competition.gym_env as gym_env

    seen: list[str | None] = []

    class FakeSubproc:
        def __init__(self, builders, start_method=None):
            seen.append(os.environ.get("OMP_NUM_THREADS"))

    monkeypatch.setattr("stable_baselines3.common.vec_env.SubprocVecEnv", FakeSubproc, raising=False)
    saved = os.environ.get("OMP_NUM_THREADS")
    os.environ.pop("OMP_NUM_THREADS", None)
    try:
        gym_env.make_vec_env(workers=2, monitor=False)
    finally:
        if saved is not None:
            os.environ["OMP_NUM_THREADS"] = saved

    assert seen == ["1"], "the children inherit whatever is set at this instant"

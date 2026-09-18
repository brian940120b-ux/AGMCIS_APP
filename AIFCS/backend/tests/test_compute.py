"""Compute detection must degrade gracefully without a GPU (PHASE 0 / rule 61)."""

from __future__ import annotations

from core.compute import detect_compute


def test_detect_compute_returns_a_usable_device():
    info = detect_compute()
    assert info["device"] in {"cpu", "cuda"}
    assert info["training_device"] in {"cpu", "cuda"}
    assert info["python"]


def test_cpu_fallback_when_torch_is_absent():
    info = detect_compute()
    if not info["torch_installed"]:
        # The platform must remain fully usable without PyTorch.
        assert info["device"] == "cpu"
        assert info["cuda_available"] is False
        assert "detail" in info

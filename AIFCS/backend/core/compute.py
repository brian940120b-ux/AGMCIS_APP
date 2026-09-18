"""Compute-device detection (GPU optional).

AIFCS must run on CPU. GPU information is reported when PyTorch is installed
and CUDA is available, and its absence is a normal, non-fatal state.
"""

from __future__ import annotations

import platform
from typing import Any


def detect_compute() -> dict[str, Any]:
    """Describe the available training/inference device."""
    info: dict[str, Any] = {
        "cpu": platform.processor() or platform.machine(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "torch_installed": False,
        "cuda_available": False,
        "gpu_name": None,
        "vram_total_mb": None,
        "device": "cpu",
        "training_device": "cpu",
    }

    try:
        import torch
    except ImportError:
        info["detail"] = "PyTorch not installed — CPU fallback (required from PHASE 11)."
        return info

    info["torch_installed"] = True
    info["torch_version"] = torch.__version__
    if torch.cuda.is_available():
        props = torch.cuda.get_device_properties(0)
        info.update(
            cuda_available=True,
            gpu_name=props.name,
            vram_total_mb=round(props.total_memory / (1024 * 1024)),
            device="cuda",
            training_device="cuda",
            detail="CUDA available.",
        )
    else:
        info["detail"] = "PyTorch installed, CUDA unavailable — CPU fallback."
    return info

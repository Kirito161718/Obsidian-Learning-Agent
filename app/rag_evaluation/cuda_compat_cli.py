from __future__ import annotations

import os
import sys
from pathlib import Path


def configure_cuda_runtime() -> dict[str, str]:
    """Load an existing CUDA Torch while retaining the project's Python stack."""
    raw_path = os.environ.get("RAG_EVALUATION_CUDA_SITE_PACKAGES", "")
    cuda_site = Path(raw_path).resolve(strict=False) if raw_path else None
    if cuda_site is None or not cuda_site.is_dir():
        raise RuntimeError(
            "RAG_EVALUATION_CUDA_SITE_PACKAGES must point to an existing site-packages directory"
        )
    os.environ.setdefault("USE_TF", "0")
    os.environ.setdefault("USE_FLAX", "0")
    sys.path.insert(0, str(cuda_site))
    try:
        import optree  # noqa: F401
        import torch
        import torchvision  # noqa: F401
    finally:
        sys.path.remove(str(cuda_site))
        sys.path.append(str(cuda_site))
    if not torch.cuda.is_available():
        raise RuntimeError("The configured Torch runtime does not provide CUDA")
    return {
        "torch_version": str(torch.__version__),
        "device": "cuda",
    }


def main() -> int:
    configure_cuda_runtime()
    from app.rag_evaluation.cli import main as evaluation_main

    return evaluation_main()


if __name__ == "__main__":
    raise SystemExit(main())

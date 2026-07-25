from functools import lru_cache
import threading
import time

import numpy as np

from app.core.config import get_settings

_status_lock = threading.Lock()
_factory_lock = threading.Lock()
_service: "EmbeddingService | None" = None
_status = {
    "status": "not_loaded",
    "initialization_ms": 0.0,
    "error_code": None,
}


class EmbeddingService:
    def __init__(self, model_name: str, device: str = "cpu", batch_size: int = 8) -> None:
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model_name, device=device)
        self.batch_size = batch_size

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self.model.encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32).tolist()

    def embed_query(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]

    @property
    def vector_size(self) -> int:
        return int(self.model.get_sentence_embedding_dimension())


@lru_cache
def get_embedding_service() -> EmbeddingService:
    global _service
    settings = get_settings()
    with _factory_lock:
        if _service is not None:
            return _service
        started = time.perf_counter()
        with _status_lock:
            _status.update(status="loading", error_code=None)
        try:
            _service = EmbeddingService(
                model_name=settings.embedding_model_name,
                device=settings.embedding_device,
                batch_size=settings.embedding_batch_size,
            )
        except Exception as exc:
            with _status_lock:
                _status.update(
                    status="error",
                    initialization_ms=(time.perf_counter() - started) * 1000,
                    error_code=type(exc).__name__,
                )
            raise
        with _status_lock:
            _status.update(
                status="ready",
                initialization_ms=(time.perf_counter() - started) * 1000,
                error_code=None,
            )
        return _service


def embedding_runtime_status() -> dict:
    with _status_lock:
        return dict(_status)


def shutdown_embedding_service() -> None:
    global _service
    with _factory_lock:
        _service = None
    get_embedding_service.cache_clear()
    with _status_lock:
        _status.update(status="not_loaded", initialization_ms=0.0, error_code=None)

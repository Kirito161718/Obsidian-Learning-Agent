import logging
import time
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any

from app.core.config import BASE_DIR, get_settings

logger = logging.getLogger(__name__)
_runtime_service: "RerankService | None" = None
_factory_lock = Lock()
_services: dict[tuple, "RerankService"] = {}


class RerankService:
    def __init__(
        self,
        enabled: bool | None = None,
        model_name: str | None = None,
        device: str | None = None,
        batch_size: int | None = None,
    ) -> None:
        settings = get_settings()
        self.enabled = settings.rerank_enabled if enabled is None else enabled
        self.model_name = model_name or settings.rerank_model
        self.configured_device = (device or settings.reranker_device).casefold()
        if self.configured_device not in {"auto", "cpu", "cuda"}:
            raise ValueError("Reranker device must be auto, cpu, or cuda")
        self.batch_size = batch_size or settings.reranker_batch_size
        self._reranker: Any | None = None
        self._backend: str | None = None
        self._active_device: str | None = None
        self._fallback_reason: str | None = None
        self._status = "not_loaded" if self.enabled else "disabled"
        self._error_code: str | None = None
        self._load_attempted = False
        self._load_lock = Lock()
        self._initialization_ms = 0.0
        logger.info(
            "Rerank enabled=%s, model=%s, device=%s, batch_size=%s",
            self.enabled,
            self.model_name,
            self.configured_device,
            self.batch_size,
        )

    def rerank(self, query: str, chunks: list[dict], top_n: int | None = None) -> list[dict]:
        settings = get_settings()
        keep_n = top_n if top_n is not None else settings.rerank_top_n
        keep_n = max(0, keep_n)
        if keep_n == 0 or not chunks:
            return []

        if not self.enabled:
            logger.info("Rerank disabled, using original Qdrant order, top_n=%s", keep_n)
            return chunks[:keep_n]

        reranker = self._get_reranker()
        if reranker is None:
            logger.warning("Rerank fallback to original Qdrant order, top_n=%s", keep_n)
            return chunks[:keep_n]

        prepared: list[dict] = []
        pairs: list[list[str]] = []
        pair_indexes: list[int] = []
        for index, chunk in enumerate(chunks):
            item = dict(chunk)
            text = self._chunk_text(item)
            item["rerank_score"] = float("-inf")
            prepared.append(item)
            if text:
                pairs.append([query, text])
                pair_indexes.append(index)

        if not pairs:
            logger.warning("Rerank fallback: no candidate text available")
            return chunks[:keep_n]

        try:
            scores = self._score_pairs(reranker, pairs)
        except Exception as exc:
            if self._active_device == "cuda" and self._fallback_to_cpu(exc):
                try:
                    scores = self._score_pairs(self._reranker, pairs)
                except Exception as fallback_exc:
                    self._mark_error(fallback_exc)
                    logger.warning(
                        "CPU rerank scoring failed after CUDA fallback; using Qdrant order",
                        exc_info=True,
                    )
                    return chunks[:keep_n]
            else:
                self._mark_error(exc)
                logger.warning(
                    "Rerank scoring failed, fallback to original Qdrant order",
                    exc_info=True,
                )
                return chunks[:keep_n]

        for index, score in zip(pair_indexes, scores, strict=True):
            prepared[index]["rerank_score"] = float(score)

        reranked = sorted(prepared, key=lambda item: item.get("rerank_score", float("-inf")), reverse=True)
        logger.info("Rerank completed, candidates=%s, kept=%s", len(chunks), keep_n)
        return reranked[:keep_n]

    def _get_reranker(self) -> Any | None:
        if self._load_attempted:
            return self._reranker
        with self._load_lock:
            if self._load_attempted:
                return self._reranker
            self._load_attempted = True
            self._status = "loading"
            started = time.perf_counter()
            try:
                return self._load_reranker()
            finally:
                self._initialization_ms = (
                    time.perf_counter() - started
                ) * 1000

    def prewarm(self) -> bool:
        if not self.enabled:
            return True
        reranker = self._get_reranker()
        if reranker is None:
            return False
        try:
            self._score_pairs(
                reranker,
                [["safe model readiness probe", "safe model readiness probe"]],
            )
            return True
        except Exception as exc:
            if self._active_device == "cuda" and self._fallback_to_cpu(exc):
                try:
                    self._score_pairs(
                        self._reranker,
                        [["safe model readiness probe", "safe model readiness probe"]],
                    )
                    return True
                except Exception as fallback_exc:
                    self._mark_error(fallback_exc)
                    return False
            self._mark_error(exc)
            return False

    def consume_initialization_ms(self) -> float:
        with self._load_lock:
            value = self._initialization_ms
            self._initialization_ms = 0.0
            return value

    def _load_reranker(self) -> Any | None:
        model = self._model_name_or_path()
        preferred = self._preferred_device()
        if self._load_on_device(model, preferred):
            return self._reranker
        if preferred == "cuda":
            self._fallback_reason = self._fallback_reason or "cuda_load_failed"
            self._release_cuda_memory()
            if self._load_on_device(model, "cpu"):
                logger.warning("CUDA reranker unavailable; automatically fell back to CPU")
                return self._reranker
        self._status = "error"
        return None

    def _load_on_device(self, model: str, device: str) -> bool:
        try:
            from FlagEmbedding import FlagReranker

            self._reranker = FlagReranker(
                model,
                use_fp16=device == "cuda",
                devices=device,
            )
            self._backend = "FlagEmbedding"
            self._active_device = device
            self._status = "ready"
            self._error_code = None
            logger.info(
                "Loaded rerank model with FlagEmbedding: model=%s, device=%s",
                self.model_name,
                device,
            )
            return True
        except Exception as exc:
            self._error_code = type(exc).__name__
            logger.info(
                "FlagEmbedding reranker unavailable; trying CrossEncoder: "
                "device=%s, error_type=%s",
                device,
                type(exc).__name__,
            )

        try:
            from sentence_transformers import CrossEncoder

            self._reranker = CrossEncoder(model, device=device)
            self._backend = "CrossEncoder"
            self._active_device = device
            self._status = "ready"
            self._error_code = None
            logger.info(
                "Loaded rerank model with CrossEncoder: model=%s, device=%s",
                self.model_name,
                device,
            )
            return True
        except Exception as exc:
            self._error_code = type(exc).__name__
            logger.warning(
                "Failed to load CrossEncoder reranker: device=%s, error_type=%s",
                device,
                type(exc).__name__,
            )
            self._reranker = None
            self._backend = None
            self._active_device = None
            return False

    def _score_pairs(self, reranker: Any, pairs: list[list[str]]) -> list[float]:
        if self._backend == "FlagEmbedding":
            try:
                scores = reranker.compute_score(
                    pairs,
                    normalize=True,
                    batch_size=self.batch_size,
                )
            except TypeError:
                scores = reranker.compute_score(pairs, normalize=True)
        else:
            try:
                scores = reranker.predict(
                    pairs,
                    batch_size=self.batch_size,
                    show_progress_bar=False,
                )
            except TypeError:
                scores = reranker.predict(pairs)

        if isinstance(scores, (int, float)):
            return [float(scores)]
        if hasattr(scores, "tolist"):
            scores = scores.tolist()
        return [float(score) for score in scores]

    def runtime_status(self) -> dict[str, Any]:
        return {
            "status": self._status,
            "configured_device": self.configured_device,
            "active_device": self._active_device,
            "backend": self._backend,
            "batch_size": self.batch_size,
            "fallback": self._fallback_reason is not None,
            "fallback_reason": self._fallback_reason,
            "error_code": self._error_code,
            "initialization_ms": self._initialization_ms,
        }

    def shutdown(self) -> None:
        self._reranker = None
        self._backend = None
        self._active_device = None
        self._load_attempted = False
        self._status = "not_loaded" if self.enabled else "disabled"
        self._release_cuda_memory()

    def _preferred_device(self) -> str:
        if self.configured_device == "cpu":
            return "cpu"
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda"
        except Exception as exc:
            self._error_code = type(exc).__name__
        if self.configured_device in {"auto", "cuda"}:
            self._fallback_reason = "cuda_unavailable"
        return "cpu"

    def _fallback_to_cpu(self, exc: Exception) -> bool:
        with self._load_lock:
            if self._active_device != "cuda":
                return self._reranker is not None and self._active_device == "cpu"
            self._fallback_reason = f"cuda_{type(exc).__name__}"
            self._error_code = type(exc).__name__
            self._reranker = None
            self._backend = None
            self._active_device = None
            self._release_cuda_memory()
            return self._load_on_device(self._model_name_or_path(), "cpu")

    def _mark_error(self, exc: Exception) -> None:
        self._error_code = type(exc).__name__
        self._status = "error"

    @staticmethod
    def _release_cuda_memory() -> None:
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            return

    def _model_name_or_path(self) -> str:
        candidate = Path(self.model_name)
        if candidate.is_absolute():
            return str(candidate)

        project_path = BASE_DIR / candidate
        if project_path.exists():
            return str(project_path)

        return self.model_name

    def _chunk_text(self, chunk: dict) -> str:
        text = chunk.get("text")
        if text:
            return str(text)

        payload = chunk.get("payload")
        if isinstance(payload, dict) and payload.get("text"):
            return str(payload["text"])

        return ""


@lru_cache
def get_rerank_service(
    enabled: bool | None = None,
    model_name: str | None = None,
    device: str | None = None,
    batch_size: int | None = None,
) -> RerankService:
    global _runtime_service
    key = (enabled, model_name, device, batch_size)
    with _factory_lock:
        service = _services.get(key)
        if service is None:
            service = RerankService(
                enabled=enabled,
                model_name=model_name,
                device=device,
                batch_size=batch_size,
            )
            _services[key] = service
        _runtime_service = service
        return service


def reranker_runtime_status() -> dict[str, Any]:
    if _runtime_service is None:
        settings = get_settings()
        return {
            "status": "not_loaded" if settings.rerank_enabled else "disabled",
            "configured_device": settings.reranker_device,
            "active_device": None,
            "backend": None,
            "batch_size": settings.reranker_batch_size,
            "fallback": False,
            "fallback_reason": None,
            "error_code": None,
            "initialization_ms": 0.0,
        }
    return _runtime_service.runtime_status()


def shutdown_rerank_service() -> None:
    global _runtime_service
    with _factory_lock:
        for service in set(_services.values()):
            service.shutdown()
        _services.clear()
        _runtime_service = None
    get_rerank_service.cache_clear()

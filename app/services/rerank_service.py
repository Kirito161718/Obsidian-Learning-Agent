import logging
from pathlib import Path
from typing import Any

from app.core.config import BASE_DIR, get_settings

logger = logging.getLogger(__name__)


class RerankService:
    def __init__(
        self,
        enabled: bool | None = None,
        model_name: str | None = None,
    ) -> None:
        settings = get_settings()
        self.enabled = settings.rerank_enabled if enabled is None else enabled
        self.model_name = model_name or settings.rerank_model
        self._reranker: Any | None = None
        self._backend: str | None = None
        self._load_attempted = False
        logger.info("Rerank enabled=%s, model=%s", self.enabled, self.model_name)

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
        except Exception:
            logger.warning("Rerank scoring failed, fallback to original Qdrant order", exc_info=True)
            return chunks[:keep_n]

        for index, score in zip(pair_indexes, scores, strict=True):
            prepared[index]["rerank_score"] = float(score)

        reranked = sorted(prepared, key=lambda item: item.get("rerank_score", float("-inf")), reverse=True)
        logger.info("Rerank completed, candidates=%s, kept=%s", len(chunks), keep_n)
        return reranked[:keep_n]

    def _get_reranker(self) -> Any | None:
        if self._load_attempted:
            return self._reranker

        self._load_attempted = True
        model = self._model_name_or_path()

        try:
            from FlagEmbedding import FlagReranker

            self._reranker = FlagReranker(model, use_fp16=False)
            self._backend = "FlagEmbedding"
            logger.info("Loaded rerank model with FlagEmbedding: %s", self.model_name)
            return self._reranker
        except Exception:
            logger.warning("Failed to load FlagEmbedding reranker, trying CrossEncoder", exc_info=True)

        try:
            from sentence_transformers import CrossEncoder

            self._reranker = CrossEncoder(model)
            self._backend = "CrossEncoder"
            logger.info("Loaded rerank model with CrossEncoder: %s", self.model_name)
            return self._reranker
        except Exception:
            logger.warning("Failed to load CrossEncoder reranker", exc_info=True)
            self._reranker = None
            self._backend = None
            return None

    def _score_pairs(self, reranker: Any, pairs: list[list[str]]) -> list[float]:
        if self._backend == "FlagEmbedding":
            scores = reranker.compute_score(pairs, normalize=True)
        else:
            scores = reranker.predict(pairs)

        if isinstance(scores, (int, float)):
            return [float(scores)]
        if hasattr(scores, "tolist"):
            scores = scores.tolist()
        return [float(score) for score in scores]

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

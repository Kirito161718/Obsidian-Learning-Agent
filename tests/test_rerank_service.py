from app.services.rerank_service import RerankService


def test_rerank_disabled_returns_original_top_n():
    chunks = [
        {"score": 0.3, "payload": {"text": "third"}},
        {"score": 0.2, "payload": {"text": "second"}},
        {"score": 0.1, "payload": {"text": "first"}},
    ]
    service = RerankService(enabled=False)

    assert service.rerank("query", chunks, top_n=2) == chunks[:2]


def test_rerank_fallback_when_model_is_unavailable(monkeypatch):
    chunks = [
        {"score": 0.8, "payload": {"text": "alpha"}},
        {"score": 0.7, "payload": {"text": "beta"}},
    ]
    service = RerankService(enabled=True, model_name="missing-rerank-model")
    monkeypatch.setattr(service, "_get_reranker", lambda: None)

    assert service.rerank("query", chunks, top_n=1) == chunks[:1]


def test_rerank_adds_scores_when_backend_succeeds(monkeypatch):
    chunks = [
        {"score": 0.7, "payload": {"text": "less relevant"}},
        {"score": 0.6, "payload": {"text": "more relevant"}},
    ]
    service = RerankService(enabled=True)
    service._backend = "CrossEncoder"
    monkeypatch.setattr(service, "_get_reranker", lambda: object())
    monkeypatch.setattr(service, "_score_pairs", lambda reranker, pairs: [0.1, 0.9])

    reranked = service.rerank("query", chunks, top_n=2)

    assert reranked[0]["payload"]["text"] == "more relevant"
    assert reranked[0]["rerank_score"] == 0.9
    assert reranked[1]["rerank_score"] == 0.1

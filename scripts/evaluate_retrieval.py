from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_QUESTIONS_PATH = PROJECT_ROOT / "tests" / "eval_questions.json"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "reports" / "retrieval_eval_result.json"
PLACEHOLDER_DOCUMENT_ID = "替换为真实 document_id"


@dataclass
class EvaluationResult:
    question: str
    expected_document_id: str
    retrieved_document_ids: list[str]
    hit_at_k: int
    reciprocal_rank: float
    matched_keywords: list[str]
    keyword_match_rate: float
    skipped: bool = False
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "expected_document_id": self.expected_document_id,
            "retrieved_document_ids": self.retrieved_document_ids,
            "hit_at_k": self.hit_at_k,
            "reciprocal_rank": self.reciprocal_rank,
            "matched_keywords": self.matched_keywords,
            "keyword_match_rate": self.keyword_match_rate,
            "skipped": self.skipped,
            "message": self.message,
        }


def load_eval_questions(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"评估集不存在：{path}. 请先创建 tests/eval_questions.json。")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"评估集不是合法 JSON：{path}") from exc

    if not isinstance(data, list):
        raise ValueError("评估集格式错误：顶层必须是 JSON 数组。")
    return data


def is_placeholder_document_id(value: str | None) -> bool:
    if not value:
        return True
    normalized = value.strip().lower()
    return (
        normalized == PLACEHOLDER_DOCUMENT_ID.lower()
        or "替换" in normalized
        or "真实 document_id" in normalized
        or normalized in {"your_document_id", "document_id", "replace_me"}
    )


def chunk_text(chunk: dict[str, Any]) -> str:
    text = chunk.get("text")
    if text:
        return str(text)

    payload = chunk.get("payload")
    if isinstance(payload, dict) and payload.get("text"):
        return str(payload["text"])

    return ""


def chunk_document_id(chunk: dict[str, Any]) -> str:
    payload = chunk.get("payload")
    if isinstance(payload, dict):
        return str(payload.get("document_id") or "")
    return str(chunk.get("document_id") or "")


def compute_hit_at_k(retrieved_document_ids: list[str], expected_document_id: str) -> int:
    return int(expected_document_id in retrieved_document_ids)


def compute_reciprocal_rank(retrieved_document_ids: list[str], expected_document_id: str) -> float:
    try:
        rank = retrieved_document_ids.index(expected_document_id) + 1
    except ValueError:
        return 0.0
    return 1.0 / rank


def match_keywords(chunks: list[dict[str, Any]], expected_keywords: list[str]) -> list[str]:
    combined_text = "\n".join(chunk_text(chunk) for chunk in chunks)
    return [keyword for keyword in expected_keywords if keyword and keyword in combined_text]


def compute_keyword_match_rate(matched_keywords: list[str], expected_keywords: list[str]) -> float:
    keywords = [keyword for keyword in expected_keywords if keyword]
    if not keywords:
        return 0.0
    return len(matched_keywords) / len(keywords)


def evaluate_retrieved_chunks(
    *,
    question: str,
    expected_document_id: str,
    expected_keywords: list[str],
    chunks: list[dict[str, Any]],
) -> EvaluationResult:
    retrieved_document_ids = [document_id for document_id in (chunk_document_id(chunk) for chunk in chunks) if document_id]
    matched = match_keywords(chunks, expected_keywords)
    return EvaluationResult(
        question=question,
        expected_document_id=expected_document_id,
        retrieved_document_ids=retrieved_document_ids,
        hit_at_k=compute_hit_at_k(retrieved_document_ids, expected_document_id),
        reciprocal_rank=compute_reciprocal_rank(retrieved_document_ids, expected_document_id),
        matched_keywords=matched,
        keyword_match_rate=compute_keyword_match_rate(matched, expected_keywords),
    )


def summarize_results(results: list[EvaluationResult]) -> dict[str, float]:
    evaluated = [result for result in results if not result.skipped]
    if not evaluated:
        return {
            "hit_rate": 0.0,
            "mrr": 0.0,
            "keyword_match_rate": 0.0,
        }

    return {
        "hit_rate": sum(result.hit_at_k for result in evaluated) / len(evaluated),
        "mrr": sum(result.reciprocal_rank for result in evaluated) / len(evaluated),
        "keyword_match_rate": sum(result.keyword_match_rate for result in evaluated) / len(evaluated),
    }


def build_retrieval_service():
    from app.services.embedding_service import get_embedding_service
    from app.services.rag_service import RagService
    from app.services.rerank_service import RerankService
    from app.services.vector_service import VectorService

    class RetrievalOnlyLlmClient:
        def rewrite_query(self, question: str) -> str:
            return " ".join(question.split()).strip()

        def generate_answer(self, question: str, contexts: list[dict]) -> str:
            raise RuntimeError("evaluate_retrieval.py must not generate answers")

    embedding_service = get_embedding_service()
    return RagService(
        embedding_service=embedding_service,
        vector_service=VectorService(vector_size=embedding_service.vector_size),
        rerank_service=RerankService(),
        llm_client=RetrievalOnlyLlmClient(),
    )


def run_evaluation(questions: list[dict[str, Any]], top_k: int) -> list[EvaluationResult]:
    service = None
    results: list[EvaluationResult] = []

    for item in questions:
        question = str(item.get("question") or "").strip()
        expected_document_id = str(item.get("expected_document_id") or "").strip()
        expected_keywords = item.get("expected_keywords") or []
        if not isinstance(expected_keywords, list):
            expected_keywords = []

        if not question:
            results.append(
                EvaluationResult(
                    question="",
                    expected_document_id=expected_document_id,
                    retrieved_document_ids=[],
                    hit_at_k=0,
                    reciprocal_rank=0.0,
                    matched_keywords=[],
                    keyword_match_rate=0.0,
                    skipped=True,
                    message="问题为空，已跳过。",
                )
            )
            continue

        if is_placeholder_document_id(expected_document_id):
            results.append(
                EvaluationResult(
                    question=question,
                    expected_document_id=expected_document_id or PLACEHOLDER_DOCUMENT_ID,
                    retrieved_document_ids=[],
                    hit_at_k=0,
                    reciprocal_rank=0.0,
                    matched_keywords=[],
                    keyword_match_rate=0.0,
                    skipped=True,
                    message="expected_document_id 仍是占位符，请替换为真实 document_id。",
                )
            )
            continue

        if service is None:
            service = build_retrieval_service()

        chunks = service.retrieve_context(
            question=question,
            top_k=top_k,
            use_query_rewrite=False,
        )
        results.append(
            evaluate_retrieved_chunks(
                question=question,
                expected_document_id=expected_document_id,
                expected_keywords=[str(keyword) for keyword in expected_keywords],
                chunks=chunks,
            )
        )

    return results


def print_results(results: list[EvaluationResult], summary: dict[str, float], top_k: int) -> None:
    for result in results:
        print(f"Question: {result.question}")
        print(f"Expected document_id: {result.expected_document_id}")
        if result.skipped:
            print(f"Skipped: {result.message}")
        else:
            print(f"Retrieved document_ids: {result.retrieved_document_ids}")
            print(f"Hit@{top_k}: {result.hit_at_k}")
            print(f"Reciprocal Rank: {result.reciprocal_rank:.4f}")
            print(f"Matched keywords: {result.matched_keywords}")
            print(f"Keyword Match Rate: {result.keyword_match_rate:.4f}")
        print()

    print("Overall:")
    print(f"Hit Rate@{top_k}: {summary['hit_rate']:.4f}")
    print(f"MRR: {summary['mrr']:.4f}")
    print(f"Keyword Match Rate: {summary['keyword_match_rate']:.4f}")


def write_report(path: Path, results: list[EvaluationResult], summary: dict[str, float], top_k: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "top_k": top_k,
        "overall": summary,
        "results": [result.to_dict() for result in results],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate RAG retrieval quality without generating answers.")
    parser.add_argument("--top-k", type=int, default=5, help="Number of retrieved chunks used for evaluation.")
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS_PATH, help="Path to eval_questions.json.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="Path to write JSON report.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    questions_path = args.questions if args.questions.is_absolute() else PROJECT_ROOT / args.questions
    output_path = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output

    try:
        questions = load_eval_questions(questions_path)
    except (FileNotFoundError, ValueError) as exc:
        print(exc)
        return 1

    if not questions:
        print(f"评估集为空：{questions_path}. 请在 tests/eval_questions.json 中添加问题。")
        return 0

    results = run_evaluation(questions, top_k=args.top_k)
    summary = summarize_results(results)
    print_results(results, summary, top_k=args.top_k)
    write_report(output_path, results, summary, top_k=args.top_k)
    print(f"\nJSON report written to: {output_path.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

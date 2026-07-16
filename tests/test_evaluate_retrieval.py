import subprocess
import sys
import shutil
from pathlib import Path
from uuid import uuid4

from scripts.evaluate_retrieval import (
    compute_hit_at_k,
    compute_keyword_match_rate,
    compute_reciprocal_rank,
    evaluate_retrieved_chunks,
    match_keywords,
    summarize_results,
)


def test_eval_questions_file_exists():
    assert Path("tests/eval_questions.json").exists()


def test_metric_helpers():
    retrieved_document_ids = ["doc-a", "doc-b", "doc-c"]

    assert compute_hit_at_k(retrieved_document_ids, "doc-b") == 1
    assert compute_hit_at_k(retrieved_document_ids, "doc-x") == 0
    assert compute_reciprocal_rank(retrieved_document_ids, "doc-a") == 1
    assert compute_reciprocal_rank(retrieved_document_ids, "doc-b") == 0.5
    assert compute_reciprocal_rank(retrieved_document_ids, "doc-x") == 0


def test_keyword_matching_and_summary():
    chunks = [
        {"payload": {"document_id": "doc-a", "text": "系统支持文档上传和向量检索。"}},
        {"payload": {"document_id": "doc-b", "text": "问答会返回 sources。"}},
    ]
    matched = match_keywords(chunks, ["文档上传", "sources", "不存在"])
    result = evaluate_retrieved_chunks(
        question="系统支持什么？",
        expected_document_id="doc-b",
        expected_keywords=["文档上传", "sources", "不存在"],
        chunks=chunks,
    )
    summary = summarize_results([result])

    assert matched == ["文档上传", "sources"]
    assert result.hit_at_k == 1
    assert result.reciprocal_rank == 0.5
    assert result.keyword_match_rate == compute_keyword_match_rate(["文档上传", "sources"], ["文档上传", "sources", "不存在"])
    assert summary["hit_rate"] == 1
    assert summary["mrr"] == 0.5


def test_evaluate_script_empty_questions_file_has_friendly_message():
    work_dir = Path("test_artifacts") / f"eval_empty_{uuid4().hex}"
    work_dir.mkdir(parents=True, exist_ok=False)
    questions_path = work_dir / "empty_eval_questions.json"
    questions_path.write_text("[]", encoding="utf-8")
    output_path = work_dir / "report.json"

    try:
        result = subprocess.run(
            [
                sys.executable,
                "scripts/evaluate_retrieval.py",
                "--questions",
                str(questions_path),
                "--output",
                str(output_path),
            ],
            cwd=Path.cwd(),
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    assert result.returncode == 0
    assert "评估集为空" in result.stdout
    assert not output_path.exists()

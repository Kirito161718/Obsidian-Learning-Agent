from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.database import get_db
from app.rag_evaluation.comparison_service import ComparisonService
from app.rag_evaluation.dataset_loader import DatasetLoader
from app.rag_evaluation.dataset_validator import DatasetValidator, build_vault_catalog
from app.rag_evaluation.experiment_service import ExperimentService
from app.rag_evaluation.maintenance import EvaluationMaintenanceService
from app.rag_evaluation.production_adapter import ProductionRagEvaluationAdapter
from app.rag_evaluation.runner import EvaluationRunner
from app.rag_evaluation.metrics import aggregate_metric_rows, percentile
from app.rag_evaluation.safety_calibration import SafetyCalibrationService
from app.retrieval.profiles import RetrievalProfileService
from app.schemas.rag_evaluation import (
    CleanupRequest,
    CompareRequest,
    DatasetSummaryOut,
    DatasetValidationOut,
    ExperimentCreateRequest,
    ExperimentOut,
    ProfileActivationRequest,
    ProfileOut,
    SafetyCalibrationRequest,
    SafetyValidationRequest,
)
from app.security.redaction import stable_hash
from app.services.crud_errors import ResourceNotFoundError, ServiceDatabaseError
from app.services.run_history_service import RunHistoryService
from app.services.rag_model_runtime import (
    prewarm_rag_models,
    rag_model_runtime_status,
)

settings = get_settings()
templates = Jinja2Templates(directory=settings.template_dir)
router = APIRouter(prefix="/agent/rag-evaluation", tags=["rag-evaluation"])
datasets = DatasetLoader(settings)
experiments = ExperimentService(settings)
profiles = RetrievalProfileService(settings)
safety_calibration = SafetyCalibrationService()


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def evaluation_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "rag_evaluation.html")


@router.get("/datasets", response_model=list[DatasetSummaryOut])
def list_datasets(db: Session = Depends(get_db)) -> list:
    return experiments.list_datasets(db)


@router.get("/datasets/{dataset_id}", response_model=DatasetSummaryOut)
def get_dataset(dataset_id: str, db: Session = Depends(get_db)):
    return experiments.get_dataset(db, dataset_id)


@router.post("/datasets/{dataset_id}/validate", response_model=DatasetValidationOut)
def validate_dataset(dataset_id: str, db: Session = Depends(get_db)):
    row = experiments.get_dataset(db, dataset_id)
    dataset = datasets.load(Path(row.storage_path).name)
    validation = DatasetValidator(build_vault_catalog(db, settings)).validate(dataset)
    row = experiments.register_dataset(
        db,
        dataset=dataset,
        dataset_file=Path(row.storage_path).name,
        validation=validation,
    )
    return DatasetValidationOut(
        dataset=DatasetSummaryOut.model_validate(row),
        **validation.model_dump(),
    )


@router.post("/experiments", response_model=ExperimentOut)
def create_experiment(
    payload: ExperimentCreateRequest,
    db: Session = Depends(get_db),
):
    try:
        dataset_row = experiments.get_dataset(db, payload.dataset_id)
        profile = profiles.get(db, payload.profile_id)
        experiment = experiments.create_experiment(
            db,
            dataset=dataset_row,
            profile=profile,
            name=payload.name,
            variant_key=payload.variant_key,
            experiment_type=payload.experiment_type,
        )
        if payload.run_now:
            dataset = datasets.load(Path(dataset_row.storage_path).name)
            EvaluationRunner(
                ProductionRagEvaluationAdapter(settings), settings=settings
            ).run(
                db,
                dataset_record=dataset_row,
                dataset=dataset,
                profile=profile,
                experiment=experiment,
                mode=payload.experiment_type,
                categories=set(payload.categories) or None,
                case_ids=set(payload.case_ids) or None,
                repeat_count=payload.repeat_count,
                seed=payload.seed,
                publish_langsmith=payload.publish_langsmith,
            )
            db.refresh(experiment)
        return experiment
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/experiments", response_model=list[ExperimentOut])
def list_experiments(db: Session = Depends(get_db)) -> list:
    return experiments.list_experiments(db)


@router.get("/experiments/{experiment_id}", response_model=ExperimentOut)
def get_experiment(experiment_id: str, db: Session = Depends(get_db)):
    return experiments.get_experiment(db, experiment_id)


@router.post("/experiments/{experiment_id}/cancel", response_model=ExperimentOut)
def cancel_experiment(experiment_id: str, db: Session = Depends(get_db)):
    try:
        return experiments.cancel(db, experiment_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/compare")
def compare_experiments(payload: CompareRequest, db: Session = Depends(get_db)) -> dict:
    try:
        rows = [
            ExperimentOut.model_validate(
                experiments.get_experiment(db, experiment_id)
            ).model_dump(mode="json")
            for experiment_id in payload.experiment_ids
        ]
        return ComparisonService.compare(rows)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/profiles", response_model=list[ProfileOut])
def list_profiles(db: Session = Depends(get_db)) -> list:
    profiles.ensure_baseline(db)
    return profiles.list(db)


@router.get("/profiles/{profile_id}", response_model=ProfileOut)
def get_profile(profile_id: str, db: Session = Depends(get_db)):
    return profiles.get(db, profile_id)


@router.post("/profiles/{profile_id}/activate", response_model=ProfileOut)
def activate_profile(
    profile_id: str,
    payload: ProfileActivationRequest,
    db: Session = Depends(get_db),
):
    try:
        profile = profiles.activate(
            db,
            profile_id,
            confirm=payload.confirm,
            expected_profile_version=payload.expected_profile_version,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    history = RunHistoryService(settings)
    run = history.create(db, run_type="rag_profile_activation", mode="activation")
    history.succeed(
        db,
        run.id,
        state={
            "context_manifest": {
                "profile_activation": {
                    "profile_version_hash": stable_hash(profile.version),
                    "rollback_profile_configured": bool(profile.rollback_profile_id),
                }
            }
        },
    )
    return profile


@router.get("/experiments/{experiment_id}/report")
def get_report(experiment_id: str, db: Session = Depends(get_db)) -> FileResponse:
    experiments.get_experiment(db, experiment_id)
    report = (settings.rag_evaluation_report_dir / experiment_id / "report.md").resolve(
        strict=False
    )
    try:
        report.relative_to(settings.rag_evaluation_report_dir.resolve(strict=False))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid report path") from exc
    if not report.is_file():
        raise HTTPException(status_code=404, detail="Report not found")
    return FileResponse(report, media_type="text/markdown", filename="report.md")


@router.get("/experiments/{experiment_id}/failures")
def get_failures(experiment_id: str, db: Session = Depends(get_db)) -> dict:
    return experiments.get_experiment(db, experiment_id).failure_summary


@router.post("/maintenance/cleanup")
def cleanup(payload: CleanupRequest, db: Session = Depends(get_db)) -> dict:
    return EvaluationMaintenanceService(settings).cleanup(db, dry_run=payload.dry_run)


@router.post("/safety/calibrate")
def calibrate_safety(
    payload: SafetyCalibrationRequest,
    db: Session = Depends(get_db),
) -> dict:
    dataset_row = experiments.get_dataset(db, payload.dataset_id)
    experiment = experiments.get_experiment(
        db, payload.baseline_experiment_id
    )
    if (
        experiment.dataset_id != dataset_row.id
        or experiment.status != "completed"
        or experiment.experiment_type != "full"
    ):
        raise HTTPException(
            status_code=400,
            detail="A completed full baseline experiment for this dataset is required",
        )
    dataset = datasets.load(Path(dataset_row.storage_path).name)
    if dataset.schema_version != "rag-eval-v2":
        raise HTTPException(
            status_code=400, detail="Safety calibration requires rag-eval-v2"
        )
    observations = safety_calibration.observations(
        dataset,
        experiments.case_results(db, experiment.id),
    )
    result = safety_calibration.calibrate(
        dataset_hash=dataset_row.content_hash,
        observations=observations,
    )
    baseline = profiles.ensure_baseline(db)
    candidates = profiles.ensure_safety_candidates(
        db,
        baseline,
        thresholds=result["thresholds"],
        dataset_hash=dataset_row.content_hash,
        calibration_config_hash=result["configuration_hash"],
    )
    return {
        **result,
        "profiles": {
            key: {"id": profile.id, "version": profile.version}
            for key, profile in candidates.items()
        },
    }


@router.post("/safety/validate")
def validate_safety(
    payload: SafetyValidationRequest,
    db: Session = Depends(get_db),
) -> dict:
    dataset_row = experiments.get_dataset(db, payload.dataset_id)
    dataset = datasets.load(Path(dataset_row.storage_path).name)
    baseline = experiments.get_experiment(
        db, payload.baseline_experiment_id
    )
    candidate = experiments.get_experiment(
        db, payload.candidate_experiment_id
    )
    if any(
        item.dataset_id != dataset_row.id
        or item.status != "completed"
        or item.experiment_type != "full"
        for item in (baseline, candidate)
    ):
        raise HTTPException(
            status_code=400,
            detail="Completed full experiments on the same dataset are required",
        )
    cases = {case.id: case for case in dataset.cases}
    baseline_rows = {
        row.case_id: row
        for row in experiments.case_results(db, baseline.id)
        if row.status == "succeeded"
        and cases.get(row.case_id)
        and cases[row.case_id].safety_split == "holdout"
    }
    candidate_rows = {
        row.case_id: row
        for row in experiments.case_results(db, candidate.id)
        if row.status == "succeeded"
        and cases.get(row.case_id)
        and cases[row.case_id].safety_split == "holdout"
    }
    shared = sorted(set(baseline_rows) & set(candidate_rows))
    if not shared:
        raise HTTPException(
            status_code=400, detail="No shared holdout results were found"
        )
    baseline_metrics = aggregate_metric_rows(
        [baseline_rows[key].metric_values for key in shared]
    )
    candidate_metrics = aggregate_metric_rows(
        [candidate_rows[key].metric_values for key in shared]
    )
    predictions = {
        key: bool(
            (candidate_rows[key].answer_stats or {}).get(
                "predicted_answerable"
            )
        )
        for key in shared
    }
    no_answer_keys = [
        key
        for key in shared
        if cases[key].expected_policy == "abstain"
    ]
    answer_keys = [
        key
        for key in shared
        if cases[key].expected_policy != "abstain"
    ]
    no_answer_correct = sum(
        not predictions[key] for key in no_answer_keys
    )
    false_refusals = sum(
        not predictions[key] for key in answer_keys
    )
    answerability_correct = sum(
        predictions[key]
        == (cases[key].expected_policy != "abstain")
        for key in shared
    )
    answer_rate = sum(predictions.values()) / len(shared)
    core = {
        "no_answer_accuracy": (
            no_answer_correct / len(no_answer_keys)
            if no_answer_keys
            else 1.0
        ),
        "false_answer_rate": (
            (len(no_answer_keys) - no_answer_correct)
            / len(no_answer_keys)
            if no_answer_keys
            else 0.0
        ),
        "answerability_accuracy": (
            answerability_correct / len(shared)
        ),
        "false_refusal_rate": (
            false_refusals / len(answer_keys) if answer_keys else 0.0
        ),
        "answer_rate": answer_rate,
    }
    safety_passed = safety_calibration.passes_core_gates(core)
    quality_limits = {
        "recall@10": 0.01,
        "ndcg@10": 0.01,
        "citation_precision": 0.01,
        "citation_recall": 0.02,
    }
    quality_deltas = {
        key: float(candidate_metrics.get(key) or 0)
        - float(baseline_metrics.get(key) or 0)
        for key in quality_limits
    }
    quality_passed = all(
        quality_deltas[key] >= -limit
        for key, limit in quality_limits.items()
    )
    baseline_warm = [
        float(row.metric_values.get("total_latency_ms") or 0)
        for row in baseline_rows.values()
        if not bool(row.metric_values.get("cold_start"))
    ]
    candidate_warm = [
        float(row.metric_values.get("total_latency_ms") or 0)
        for row in candidate_rows.values()
        if not bool(row.metric_values.get("cold_start"))
    ]
    baseline_p95 = percentile(baseline_warm, 0.95)
    candidate_p95 = percentile(candidate_warm, 0.95)
    latency_passed = bool(
        baseline_p95 is None
        or candidate_p95 is None
        or candidate_p95 <= baseline_p95 * 1.10
    )
    baseline_calls = sum(
        row.model_call_count for row in baseline_rows.values()
    ) / len(baseline_rows)
    candidate_calls = sum(
        row.model_call_count for row in candidate_rows.values()
    ) / len(candidate_rows)
    model_calls_passed = candidate_calls <= baseline_calls
    passed = (
        safety_passed
        and quality_passed
        and latency_passed
        and model_calls_passed
    )
    promotable = None
    if passed:
        candidate_profile = profiles.get(db, candidate.profile_id)
        promotable = profiles.ensure_promotable_safety_profile(
            db, candidate_profile
        )
    validation = {
        "passed": passed,
        "holdout_case_count": len(shared),
        "safety": {**core, "passed": safety_passed},
        "all_abstain": answer_rate == 0,
        "quality_deltas": quality_deltas,
        "quality_passed": quality_passed,
        "warm_full_rag_p95": {
            "baseline_ms": baseline_p95,
            "candidate_ms": candidate_p95,
            "passed": latency_passed,
        },
        "model_calls": {
            "baseline_mean": baseline_calls,
            "candidate_mean": candidate_calls,
            "passed": model_calls_passed,
        },
        "promotable_profile": (
            {"id": promotable.id, "version": promotable.version}
            if promotable
            else None
        ),
        "activated": False,
    }
    candidate.metric_summary = {
        **dict(candidate.metric_summary or {}),
        "safety_validation": validation,
    }
    db.commit()
    return validation


@router.get("/safety/status")
def safety_status(db: Session = Depends(get_db)) -> dict:
    profile_rows = profiles.list(db)
    active = next((item for item in profile_rows if item.is_active), None)
    safe = next(
        (item for item in profile_rows if item.version == "rag-safe-v1"),
        None,
    )
    dataset_rows = experiments.list_datasets(db)
    v2 = next(
        (item for item in dataset_rows if item.version == "obsidian-real-v2"),
        None,
    )
    split_counts = {"calibration": 0, "holdout": 0}
    if v2 is not None:
        dataset = datasets.load(Path(v2.storage_path).name)
        for case in dataset.cases:
            if case.safety_split in split_counts:
                split_counts[case.safety_split] += 1
    return {
        "dataset_version": v2.version if v2 else None,
        "dataset_case_count": v2.case_count if v2 else 0,
        "split_counts": split_counts,
        "active_profile": active.version if active else None,
        "safe_profile": (
            {
                "id": safe.id,
                "version": safe.version,
                "promotion_eligible": safe.promotion_eligible,
                "is_active": safe.is_active,
                "configuration": safe.configuration,
            }
            if safe
            else None
        ),
    }


@router.get("/performance/profile")
def performance_profile(
    experiment_id: str,
    db: Session = Depends(get_db),
) -> dict:
    rows = [
        row
        for row in experiments.case_results(db, experiment_id)
        if row.status == "succeeded"
    ]
    cold = [
        float(row.metric_values.get("total_latency_ms") or 0)
        for row in rows
        if bool(row.metric_values.get("cold_start"))
    ]
    warm = [
        float(row.metric_values.get("total_latency_ms") or 0)
        for row in rows
        if not bool(row.metric_values.get("cold_start"))
    ]
    stage_keys = sorted(
        {
            key
            for row in rows
            for key in row.metric_values
            if key.startswith("stage_") and key.endswith("_ms")
        }
    )
    stage_means = {
        key.removeprefix("stage_"): (
            sum(
                float(row.metric_values.get(key) or 0)
                for row in rows
            )
            / len(rows)
            if rows
            else 0
        )
        for key in stage_keys
    }
    slowest = sorted(
        (
            {
                "case_id": row.case_id,
                "latency_ms": row.metric_values.get("total_latency_ms"),
            }
            for row in rows
        ),
        key=lambda item: float(item["latency_ms"] or 0),
        reverse=True,
    )[:10]
    return {
        "experiment_id": experiment_id,
        "cold": {
            "count": len(cold),
            "p50_ms": percentile(cold, 0.50),
            "p95_ms": percentile(cold, 0.95),
        },
        "warm": {
            "count": len(warm),
            "p50_ms": percentile(warm, 0.50),
            "p95_ms": percentile(warm, 0.95),
        },
        "stage_mean_ms": stage_means,
        "slowest_cases": slowest,
    }


@router.post("/performance/prewarm")
def prewarm_models() -> dict:
    if not settings.rag_model_prewarm_enabled:
        raise HTTPException(
            status_code=409,
            detail="Set RAG_MODEL_PREWARM_ENABLED=true before manual prewarm",
        )
    return prewarm_rag_models(settings)


@router.get("/performance/prewarm/status")
def prewarm_status() -> dict:
    return rag_model_runtime_status()

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
)
from app.security.redaction import stable_hash
from app.services.crud_errors import ResourceNotFoundError, ServiceDatabaseError
from app.services.run_history_service import RunHistoryService

settings = get_settings()
templates = Jinja2Templates(directory=settings.template_dir)
router = APIRouter(prefix="/agent/rag-evaluation", tags=["rag-evaluation"])
datasets = DatasetLoader(settings)
experiments = ExperimentService(settings)
profiles = RetrievalProfileService(settings)


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

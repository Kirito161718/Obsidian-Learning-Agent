from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.core.config import get_settings
from app.db.database import SessionLocal
from app.db.models import RagRetrievalProfile
from app.rag_evaluation.comparison_service import ComparisonService
from app.rag_evaluation.dataset_loader import DatasetLoader
from app.rag_evaluation.dataset_builder import RealDatasetBuilder
from app.rag_evaluation.dataset_validator import DatasetValidator, build_vault_catalog
from app.rag_evaluation.experiment_service import ExperimentService
from app.rag_evaluation.production_adapter import ProductionRagEvaluationAdapter
from app.rag_evaluation.runner import EvaluationRunner
from app.rag_evaluation.shadow_collection import ShadowCollectionService
from app.retrieval.profiles import RetrievalProfileService
from app.schemas.rag_evaluation import ExperimentOut


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reproducible RAG evaluation")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate-dataset")
    validate.add_argument("--file")
    build = subparsers.add_parser("build-dataset")
    build.add_argument("--file", default="obsidian-real-v1.yaml")
    build.add_argument("--force", action="store_true")

    run = subparsers.add_parser("run")
    run.add_argument("--dataset-file")
    run.add_argument("--profile", default="rag-baseline-v1")
    run.add_argument("--mode", choices=["retrieval", "full"], default="retrieval")
    run.add_argument("--category", action="append", default=[])
    run.add_argument("--case-id", action="append", default=[])
    run.add_argument("--repeat-count", type=int)
    run.add_argument("--seed", type=int, default=7)
    run.add_argument("--name")
    run.add_argument("--resume-experiment")
    run.add_argument("--no-langsmith", action="store_true")

    compare = subparsers.add_parser("compare")
    compare.add_argument("experiment_ids", nargs="+")
    prepare_shadow = subparsers.add_parser("prepare-shadow")
    prepare_shadow.add_argument("--dataset-file")
    prepare_shadow.add_argument("--collection")
    prepare_shadow.add_argument("--replace", action="store_true")
    analyze_chunks = subparsers.add_parser("analyze-chunks")
    analyze_chunks.add_argument("--collection")
    return parser


def _dataset_file(loader: DatasetLoader, value: str | None) -> str:
    if value:
        return Path(value).name
    files = [
        path
        for path in loader.list_files()
        if path.name != "example.dataset.yaml"
    ]
    if not files:
        raise SystemExit("No local evaluation dataset found")
    return files[0].name


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    loader = DatasetLoader(settings)
    service = ExperimentService(settings)
    with SessionLocal() as db:
        if args.command == "build-dataset":
            builder = RealDatasetBuilder(settings)
            dataset = (
                builder.build_v2(db)
                if Path(args.file).stem == "obsidian-real-v2"
                else builder.build(db)
            )
            path = builder.write(dataset, file_name=args.file, overwrite=args.force)
            print(
                json.dumps(
                    {
                        "dataset_file": path.name,
                        "version": dataset.version,
                        "case_count": len(dataset.cases),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        if args.command == "validate-dataset":
            file_name = _dataset_file(loader, args.file)
            dataset = loader.load(file_name)
            validation = DatasetValidator(build_vault_catalog(db, settings)).validate(
                dataset
            )
            record = service.register_dataset(
                db,
                dataset=dataset,
                dataset_file=file_name,
                validation=validation,
            )
            print(
                json.dumps(
                    {
                        "dataset_id": record.id,
                        **validation.model_dump(),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0 if validation.valid else 2

        if args.command == "compare":
            if not 2 <= len(args.experiment_ids) <= 6:
                raise SystemExit("Compare requires between 2 and 6 experiment IDs")
            rows = [
                ExperimentOut.model_validate(
                    service.get_experiment(db, experiment_id)
                ).model_dump(mode="json")
                for experiment_id in args.experiment_ids
            ]
            print(
                json.dumps(
                    ComparisonService.compare(rows),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        if args.command == "analyze-chunks":
            collection_name = args.collection or settings.qdrant_collection
            print(
                json.dumps(
                    {
                        "collection_name": collection_name,
                        "statistics": ShadowCollectionService(settings).analyze(
                            collection_name=collection_name
                        ),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        if args.command == "prepare-shadow":
            file_name = _dataset_file(loader, args.dataset_file)
            dataset = loader.load(file_name)
            validation = DatasetValidator(
                build_vault_catalog(db, settings)
            ).validate(dataset)
            record = service.register_dataset(
                db,
                dataset=dataset,
                dataset_file=file_name,
                validation=validation,
            )
            if not validation.valid:
                raise SystemExit("Dataset validation failed")
            shadow_service = ShadowCollectionService(settings)
            collection_name = args.collection or shadow_service.default_name(
                record.content_hash
            )
            result = shadow_service.prepare(
                db,
                collection_name=collection_name,
                replace=args.replace,
            )
            profile_service = RetrievalProfileService(settings)
            baseline = profile_service.ensure_baseline(db)
            candidates = profile_service.ensure_candidates(
                db,
                baseline,
                shadow_collection=collection_name,
            )
            result["profiles"] = {
                key: profile.version for key, profile in candidates.items()
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        file_name = _dataset_file(loader, args.dataset_file)
        dataset = loader.load(file_name)
        validation = DatasetValidator(build_vault_catalog(db, settings)).validate(
            dataset
        )
        record = service.register_dataset(
            db,
            dataset=dataset,
            dataset_file=file_name,
            validation=validation,
        )
        if not validation.valid:
            raise SystemExit("Dataset validation failed")
        profile_service = RetrievalProfileService(settings)
        profile_service.ensure_baseline(db)
        existing_experiment = (
            service.get_experiment(db, args.resume_experiment)
            if args.resume_experiment
            else None
        )
        run_mode = (
            existing_experiment.experiment_type
            if existing_experiment is not None
            else args.mode
        )
        profile = (
            db.get(RagRetrievalProfile, existing_experiment.profile_id)
            if existing_experiment is not None
            else db.query(RagRetrievalProfile)
            .filter(RagRetrievalProfile.version == args.profile)
            .one_or_none()
        )
        if profile is None:
            raise SystemExit(f"Retrieval profile not found: {args.profile}")
        name = args.name or (
            f"ola-rag-{profile.version}-{run_mode}-{dataset.version}"
        )
        experiment = existing_experiment or service.create_experiment(
            db,
            dataset=record,
            profile=profile,
            name=name,
            variant_key=profile.profile_key,
            experiment_type=run_mode,
        )
        results = EvaluationRunner(
            ProductionRagEvaluationAdapter(settings),
            settings=settings,
        ).run(
            db,
            dataset_record=record,
            dataset=dataset,
            profile=profile,
            experiment=experiment,
            mode=run_mode,
            categories=set(args.category) or None,
            case_ids=set(args.case_id) or None,
            repeat_count=args.repeat_count,
            seed=args.seed,
            publish_langsmith=not args.no_langsmith,
        )
        db.refresh(experiment)
        print(
            json.dumps(
                {
                    "experiment_id": experiment.id,
                    "status": experiment.status,
                    "case_runs": len(results),
                    "metric_summary": experiment.metric_summary,
                    "report_dir": (
                        Path("reports") / "rag_evaluation" / experiment.id
                    ).as_posix(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if experiment.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

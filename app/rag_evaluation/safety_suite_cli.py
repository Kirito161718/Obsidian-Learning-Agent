from __future__ import annotations

import argparse
import json

from app.core.config import get_settings
from app.db.database import SessionLocal
from app.db.models import RagRetrievalProfile
from app.rag_evaluation.dataset_loader import DatasetLoader
from app.rag_evaluation.dataset_validator import (
    DatasetValidator,
    build_vault_catalog,
)
from app.rag_evaluation.experiment_service import ExperimentService
from app.rag_evaluation.production_adapter import (
    ProductionRagEvaluationAdapter,
)
from app.rag_evaluation.runner import EvaluationRunner


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run ordered Stage 7E profiles in one shared model process"
    )
    parser.add_argument("--dataset-file", default="obsidian-real-v2.yaml")
    parser.add_argument("--profile", action="append", required=True)
    parser.add_argument(
        "--split", choices=["calibration", "holdout", "all"], required=True
    )
    parser.add_argument("--repeat-count", type=int, default=1)
    parser.add_argument("--name-prefix", default="ola-rag-stage7e")
    args = parser.parse_args()
    settings = get_settings()
    loader = DatasetLoader(settings)
    service = ExperimentService(settings)
    adapter = ProductionRagEvaluationAdapter(settings)
    runner = EvaluationRunner(adapter, settings=settings)
    completed: list[dict] = []
    with SessionLocal() as db:
        dataset = loader.load(args.dataset_file)
        validation = DatasetValidator(
            build_vault_catalog(db, settings)
        ).validate(dataset)
        record = service.register_dataset(
            db,
            dataset=dataset,
            dataset_file=args.dataset_file,
            validation=validation,
        )
        if not validation.valid:
            raise SystemExit("Dataset validation failed")
        case_ids = (
            None
            if args.split == "all"
            else {
                case.id
                for case in dataset.cases
                if case.safety_split == args.split
            }
        )
        for profile_version in args.profile:
            profile = (
                db.query(RagRetrievalProfile)
                .filter(RagRetrievalProfile.version == profile_version)
                .one_or_none()
            )
            if profile is None:
                raise SystemExit(
                    f"Retrieval profile not found: {profile_version}"
                )
            name = (
                f"{args.name_prefix}-{profile_version}-{args.split}"
                f"-r{args.repeat_count}"
            )
            experiment = service.create_experiment(
                db,
                dataset=record,
                profile=profile,
                name=name,
                variant_key=profile.profile_key,
                experiment_type="full",
            )
            results = runner.run(
                db,
                dataset_record=record,
                dataset=dataset,
                profile=profile,
                experiment=experiment,
                mode="full",
                case_ids=case_ids,
                repeat_count=args.repeat_count,
                publish_langsmith=False,
            )
            completed.append(
                {
                    "experiment_id": experiment.id,
                    "profile_version": profile.version,
                    "case_runs": len(results),
                    "status": experiment.status,
                }
            )
    print(json.dumps(completed, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

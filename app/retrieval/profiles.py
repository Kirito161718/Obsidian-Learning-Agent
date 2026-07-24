from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import RagRetrievalProfile
from app.retrieval.configuration import (
    RetrievalProfileConfiguration,
    ablation_configurations,
    candidate_configurations,
)
from app.services.crud_errors import ResourceNotFoundError, ServiceDatabaseError


class RetrievalProfileService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def ensure_baseline(self, db: Session) -> RagRetrievalProfile:
        existing = (
            db.query(RagRetrievalProfile)
            .filter(RagRetrievalProfile.version == self.settings.rag_retrieval_profile_version)
            .one_or_none()
        )
        if existing is not None:
            return existing
        configuration = RetrievalProfileConfiguration.baseline(self.settings)
        active_exists = (
            db.query(RagRetrievalProfile.id)
            .filter(RagRetrievalProfile.is_active.is_(True))
            .first()
            is not None
        )
        profile = RagRetrievalProfile(
            profile_key="rag-baseline",
            version=self.settings.rag_retrieval_profile_version,
            description="Snapshot of the production retrieval configuration before stage 7 optimization.",
            configuration=configuration.canonical_dict(),
            configuration_hash=configuration.content_hash(),
            collection_name=configuration.collection_name,
            is_active=not active_exists,
            is_baseline=True,
            evaluation_status="pending",
            activated_at=datetime.now(UTC) if not active_exists else None,
        )
        db.add(profile)
        self._commit(db, "Failed to create baseline retrieval profile")
        db.refresh(profile)
        return profile

    def create(
        self,
        db: Session,
        *,
        profile_key: str,
        version: str,
        description: str,
        configuration: RetrievalProfileConfiguration,
        is_baseline: bool = False,
    ) -> RagRetrievalProfile:
        if not profile_key.strip() or not version.strip():
            raise ValueError("profile_key and version are required")
        profile = RagRetrievalProfile(
            profile_key=profile_key.strip()[:120],
            version=version.strip()[:80],
            description=description.strip() or None,
            configuration=configuration.canonical_dict(),
            configuration_hash=configuration.content_hash(),
            collection_name=configuration.collection_name,
            is_active=False,
            is_baseline=is_baseline,
            evaluation_status="pending",
        )
        db.add(profile)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ValueError("Retrieval profile version already exists") from exc
        except SQLAlchemyError as exc:
            db.rollback()
            raise ServiceDatabaseError("Failed to create retrieval profile") from exc
        db.refresh(profile)
        return profile

    def ensure_ablations(
        self,
        db: Session,
        baseline: RagRetrievalProfile,
    ) -> dict[str, RagRetrievalProfile]:
        baseline_configuration = RetrievalProfileConfiguration.model_validate(
            baseline.configuration
        )
        variants = ablation_configurations(baseline_configuration)
        result: dict[str, RagRetrievalProfile] = {"baseline": baseline}
        for key, configuration in variants.items():
            if key == "baseline":
                continue
            version = f"rag-ablation-{key}-v1"
            existing = (
                db.query(RagRetrievalProfile)
                .filter(RagRetrievalProfile.version == version)
                .one_or_none()
            )
            result[key] = existing or self.create(
                db,
                profile_key=f"rag-ablation-{key}",
                version=version,
                description=f"Stage 7B isolated ablation: {key}.",
                configuration=configuration,
            )
        return result

    def ensure_candidates(
        self,
        db: Session,
        baseline: RagRetrievalProfile,
        *,
        shadow_collection: str,
    ) -> dict[str, RagRetrievalProfile]:
        baseline_configuration = RetrievalProfileConfiguration.model_validate(
            baseline.configuration
        )
        variants = candidate_configurations(
            baseline_configuration,
            shadow_collection=shadow_collection,
        )
        result: dict[str, RagRetrievalProfile] = {}
        for key, configuration in variants.items():
            version = f"rag-candidate-{key}-v1"
            existing = (
                db.query(RagRetrievalProfile)
                .filter(RagRetrievalProfile.version == version)
                .one_or_none()
            )
            if existing is not None:
                if existing.configuration_hash != configuration.content_hash():
                    raise ValueError(
                        f"Candidate profile version has different configuration: {version}"
                    )
                result[key] = existing
                continue
            result[key] = self.create(
                db,
                profile_key=f"rag-candidate-{key}",
                version=version,
                description=f"Stage 7C evidence-driven candidate: {key}.",
                configuration=configuration,
            )
        return result

    def list(self, db: Session) -> list[RagRetrievalProfile]:
        try:
            return (
                db.query(RagRetrievalProfile)
                .order_by(RagRetrievalProfile.created_at.desc())
                .all()
            )
        except SQLAlchemyError as exc:
            raise ServiceDatabaseError("Failed to list retrieval profiles") from exc

    def get(self, db: Session, profile_id: str) -> RagRetrievalProfile:
        try:
            profile = db.get(RagRetrievalProfile, profile_id)
        except SQLAlchemyError as exc:
            raise ServiceDatabaseError("Failed to get retrieval profile") from exc
        if profile is None:
            raise ResourceNotFoundError("Retrieval profile not found")
        return profile

    def activate(
        self,
        db: Session,
        profile_id: str,
        *,
        confirm: bool,
        expected_profile_version: str,
    ) -> RagRetrievalProfile:
        if not confirm:
            raise ValueError("Profile activation requires confirm=true")
        profile = self.get(db, profile_id)
        if profile.version != expected_profile_version:
            raise ValueError("Profile version changed; refresh before activation")
        if profile.evaluation_status != "completed":
            raise ValueError("Only a completed evaluation profile can be activated")
        if not (profile.promotion_eligible or profile.manual_exception or profile.is_baseline):
            raise ValueError("Profile does not satisfy promotion safeguards")
        previous = (
            db.query(RagRetrievalProfile)
            .filter(RagRetrievalProfile.is_active.is_(True))
            .one_or_none()
        )
        now = datetime.now(UTC)
        try:
            db.query(RagRetrievalProfile).filter(
                RagRetrievalProfile.is_active.is_(True)
            ).update(
                {
                    RagRetrievalProfile.is_active: False,
                    RagRetrievalProfile.activated_at: None,
                },
                synchronize_session=False,
            )
            profile.is_active = True
            profile.activated_at = now
            if previous and previous.id != profile.id:
                profile.rollback_profile_id = previous.id
            db.commit()
        except SQLAlchemyError as exc:
            db.rollback()
            raise ServiceDatabaseError("Failed to activate retrieval profile") from exc
        db.refresh(profile)
        return profile

    @staticmethod
    def _commit(db: Session, message: str) -> None:
        try:
            db.commit()
        except SQLAlchemyError as exc:
            db.rollback()
            raise ServiceDatabaseError(message) from exc

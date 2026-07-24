from __future__ import annotations

import argparse
import json

from app.db.database import SessionLocal
from app.rag_evaluation.shadow_collection import ShadowCollectionService
from app.retrieval.profiles import RetrievalProfileService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build an isolated RAG shadow index")
    parser.add_argument("--collection", required=True)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        shadow = ShadowCollectionService()
        result = shadow.prepare(
            db,
            collection_name=args.collection,
            replace=args.replace,
        )
        profiles = RetrievalProfileService()
        baseline = profiles.ensure_baseline(db)
        result["profiles"] = {
            key: profile.version
            for key, profile in profiles.ensure_candidates(
                db,
                baseline,
                shadow_collection=result["collection_name"],
            ).items()
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

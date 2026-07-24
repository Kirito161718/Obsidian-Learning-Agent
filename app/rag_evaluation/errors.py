class RagEvaluationError(RuntimeError):
    code = "rag_evaluation_error"


class DatasetPathError(RagEvaluationError):
    code = "dataset_path_invalid"


class DatasetValidationError(RagEvaluationError):
    code = "dataset_validation_failed"


class ExperimentCancelled(RagEvaluationError):
    code = "experiment_cancelled"


class ExperimentConflictError(RagEvaluationError):
    code = "experiment_conflict"


class PromotionRejectedError(RagEvaluationError):
    code = "promotion_rejected"

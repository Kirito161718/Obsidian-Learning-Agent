class ResourceNotFoundError(LookupError):
    pass


class InvalidReferenceError(ValueError):
    def __init__(self, missing_ids: list[str]) -> None:
        self.missing_ids = missing_ids
        super().__init__(f"Unknown learning goal IDs: {', '.join(missing_ids)}")


class ServiceDatabaseError(RuntimeError):
    pass

class OfficialSourceError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(message)


def official_error(code: str, message: str, *, status_code: int = 400) -> OfficialSourceError:
    return OfficialSourceError(code, message, status_code=status_code)

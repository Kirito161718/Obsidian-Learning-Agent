from __future__ import annotations

from app.core.config import get_settings
from app.observability.configuration import ObservabilityConfiguration
from app.observability.manager import ObservabilityManager

_manager: ObservabilityManager | None = None
_test_override: ObservabilityManager | None = None

def get_observability() -> ObservabilityManager:
    global _manager
    if _test_override is not None:
        return _test_override
    if _manager is None:
        _manager = ObservabilityManager(
            ObservabilityConfiguration.from_settings(get_settings())
        )
    return _manager


def set_observability_for_tests(manager: ObservabilityManager | None) -> None:
    global _test_override
    _test_override = manager


def reset_observability() -> None:
    global _manager, _test_override
    _manager = None
    _test_override = None

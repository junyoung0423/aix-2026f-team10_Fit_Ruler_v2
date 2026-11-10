"""핏 판정 계산기 (#2). 외부 SDK·`adapters/`·`server/`를 import하지 않는 순수 로직이다."""

from fitting.fit.engine import FitEngineError, RuleFitCalculator, compute_report, to_decimal
from fitting.fit.rules import FIT_RULES_V0, FIT_RULES_VERSION, FitRules

__all__ = [
    "FIT_RULES_V0",
    "FIT_RULES_VERSION",
    "FitEngineError",
    "FitRules",
    "RuleFitCalculator",
    "compute_report",
    "to_decimal",
]

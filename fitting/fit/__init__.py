"""핏 판정 계산기 (#2). 외부 SDK·`adapters/`·`server/`를 import하지 않는 순수 로직이다.

기준표는 `config/fit_rules_v0.yaml`에서 읽는다(`load_fit_rules`). 다른 기준표는 경로를 넘긴다.
"""

from fitting.fit.engine import FitEngineError, RuleFitCalculator, compute_report, to_decimal
from fitting.fit.loader import (
    DEFAULT_RULES_PATH,
    FitRulesError,
    default_fit_rules,
    load_fit_rules,
    parse_fit_rules,
)
from fitting.fit.rules import Band, BodyLengthEstimate, FitRules, RegionRule

__all__ = [
    "DEFAULT_RULES_PATH",
    "Band",
    "BodyLengthEstimate",
    "FitEngineError",
    "FitRules",
    "FitRulesError",
    "RegionRule",
    "RuleFitCalculator",
    "compute_report",
    "default_fit_rules",
    "load_fit_rules",
    "parse_fit_rules",
    "to_decimal",
]

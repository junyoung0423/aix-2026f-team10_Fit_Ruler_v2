"""핏 계산기가 B1의 `FitCalculator` 포트로 피팅 흐름에 들어가는지 확인한다 (#2)."""

from datetime import datetime, timezone

from fitting.fit import RuleFitCalculator, compute_report
from fitting.ports import FitCalculator

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_rule_calculator_matches_compute_report(body, make_garment):
    garment = make_garment()
    calculator: FitCalculator = RuleFitCalculator()
    report = calculator.calculate(body, garment, job_id="job_1", created_at=NOW)
    assert report == compute_report(body, garment, job_id="job_1", created_at=NOW)
    assert report.rules_version == "fit_rules_v0"

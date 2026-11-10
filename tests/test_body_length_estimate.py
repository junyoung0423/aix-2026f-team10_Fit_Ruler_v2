"""총장 추정 표시 (#2, 활동지 61행). 기본은 꺼져 있고, 켜면 결과에 추정임을 표시한다."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
import yaml

from fitting.domain import FitLabel, ReasonCode, Region
from fitting.fit import (
    DEFAULT_RULES_PATH,
    FitRulesError,
    compute_report,
    default_fit_rules,
    load_fit_rules,
    parse_fit_rules,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _raw() -> dict:
    return yaml.safe_load(DEFAULT_RULES_PATH.read_text(encoding="utf-8"))


def _rules(**estimate):
    data = _raw()
    data["body_length_estimate"] = estimate
    return parse_fit_rules(data)


def _length(report):
    return next(r for r in report.regions if r.region is Region.BODY_LENGTH)


def test_default_is_off_and_report_is_unchanged(body, make_garment):
    rules = default_fit_rules()
    assert rules.body_length_estimate.enabled is False
    result = _length(compute_report(body, make_garment(), job_id="j", created_at=NOW))
    assert result.is_estimated is False
    assert result.body_cm is None
    assert result.ease_cm is None
    assert result.label is FitLabel.NOT_EVALUATED
    assert result.reason_code is ReasonCode.BODY_INPUT_NOT_PROVIDED
    assert result.explanation == (
        "기장 실측 70.0 cm입니다. 몸통 길이 입력이 없어 적정 기장은 판단하지 않습니다."
    )


def test_missing_section_means_off(body, make_garment):
    data = _raw()
    del data["body_length_estimate"]
    rules = parse_fit_rules(data)
    assert rules.body_length_estimate.enabled is False
    on_default = compute_report(body, make_garment(), job_id="j", created_at=NOW)
    assert compute_report(body, make_garment(), job_id="j", created_at=NOW, rules=rules) == (
        on_default
    )


# 키 175 기준, 비율 0.4 → 추정 70.0. (의류 기장, 기대 차이, 설명에 들어갈 관계)
ESTIMATE_CASES = [
    (70.0, 0.0, "70.0 cm와 같습니다"),
    (70.04, 0.04, "70.0 cm와 거의 같습니다"),
    (72.5, 2.5, "70.0 cm보다 2.5 cm 깁니다"),
    (66.95, -3.05, "70.0 cm보다 3.1 cm 짧습니다"),  # 표시만 반올림한다 (3.05 → 3.1)
]


@pytest.mark.parametrize(("length", "diff", "relation"), ESTIMATE_CASES)
def test_enabled_marks_the_result_as_estimated(body, make_garment, length, diff, relation):
    rules = _rules(enabled=True, method="height_ratio", ratio=0.4)
    garment = make_garment(length=length)
    report = compute_report(body, garment, job_id="j", created_at=NOW, rules=rules)
    result = _length(report)
    assert result.is_estimated is True
    assert result.body_cm == pytest.approx(70.0)
    assert result.garment_cm == length
    assert result.ease_cm == pytest.approx(diff)
    assert Decimal(repr(result.ease_cm)) == Decimal(repr(length)) - Decimal("70.0")  # 반올림 없음
    assert result.label is FitLabel.NOT_EVALUATED  # 추정값으로 적정 기장을 판정하지 않는다
    assert "추정" in result.explanation
    assert relation in result.explanation
    # 다른 부위와 종합 문구는 그대로다
    assert report.summary == "가슴 기본 여유 · 어깨 기본 범위 · 허리 넉넉"
    for other in report.regions:
        if other.region is not Region.BODY_LENGTH:
            assert other.is_estimated is False


def test_enabled_but_no_garment_length_is_not_estimated(body, make_garment):
    rules = _rules(enabled=True, ratio=0.4)
    result = _length(
        compute_report(body, make_garment(length=None), job_id="j", created_at=NOW, rules=rules)
    )
    assert result.is_estimated is False
    assert result.reason_code is ReasonCode.MEASUREMENT_MISSING


@pytest.mark.parametrize(
    "estimate",
    [
        {"enabled": True},  # 켰는데 비율 없음
        {"enabled": True, "ratio": 0},
        {"enabled": True, "ratio": 1.2},
        {"enabled": True, "ratio": "0.4"},
        {"enabled": "yes", "ratio": 0.4},
        {"enabled": True, "method": "arm_ratio", "ratio": 0.4},
        {"enabled": False, "ratio": 0.4, "unknown": 1},
        {"ratio": 0.4},  # enabled 누락
    ],
)
def test_invalid_estimate_settings_are_rejected(estimate):
    with pytest.raises(FitRulesError, match="body_length_estimate"):
        _rules(**estimate)


def test_estimate_settings_from_file(tmp_path, body, make_garment):
    data = _raw()
    data["body_length_estimate"]["enabled"] = True
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    rules = load_fit_rules(path)
    assert rules.body_length_estimate.ratio == Decimal("0.4")
    assert _length(
        compute_report(body, make_garment(), job_id="j", created_at=NOW, rules=rules)
    ).is_estimated

"""핏 판정 경계 테스트 (명세서 4장 '규칙 데이터와 경계 테스트')."""

from datetime import datetime, timezone

import pytest

from fitting.domain import ErrorCode, FitLabel, GarmentStatus, ReasonCode, Region
from fitting.domain.garment import MAX_GARMENT_CM
from fitting.fit import FitEngineError, compute_report

FIXED_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _region(report, region: Region):
    return next(r for r in report.regions if r.region is region)


def _run(body, garment):
    return compute_report(body, garment, job_id="job_test", created_at=FIXED_TIME)


# 몸 가슴 100.0 기준. (의류 둘레, 기대 여유량, 기대 판정)
CHEST_CASES = [
    (99.9, -0.1, FitLabel.INSUFFICIENT),
    (100.0, 0.0, FitLabel.SNUG),
    (105.9, 5.9, FitLabel.SNUG),
    (106.0, 6.0, FitLabel.REGULAR),
    (114.0, 14.0, FitLabel.REGULAR),
    (114.1, 14.1, FitLabel.LOOSE),
]


@pytest.mark.parametrize(("garment_cm", "ease", "label"), CHEST_CASES)
def test_chest_band_boundaries(body, make_garment, garment_cm, ease, label):
    result = _region(_run(body, make_garment(chest=garment_cm)), Region.CHEST)
    assert result.ease_cm == pytest.approx(ease)
    assert result.label is label


# 몸 허리 80.0 기준.
WAIST_CASES = [
    (79.9, FitLabel.INSUFFICIENT),
    (80.0, FitLabel.SNUG),
    (83.9, FitLabel.SNUG),
    (84.0, FitLabel.REGULAR),
    (92.0, FitLabel.REGULAR),
    (92.1, FitLabel.LOOSE),
]


@pytest.mark.parametrize(("garment_cm", "label"), WAIST_CASES)
def test_waist_band_boundaries(body, make_garment, garment_cm, label):
    result = _region(_run(body, make_garment(waist=garment_cm)), Region.WAIST)
    assert result.label is label


# 몸 어깨 44.0 기준. (의류 어깨너비, 기대 여유량, 기대 판정)
SHOULDER_CASES = [
    (42.9, -1.1, FitLabel.NARROW),
    (43.0, -1.0, FitLabel.REGULAR),
    (46.0, 2.0, FitLabel.REGULAR),
    (46.1, 2.1, FitLabel.DROPPED),
]


@pytest.mark.parametrize(("garment_cm", "ease", "label"), SHOULDER_CASES)
def test_shoulder_band_boundaries(body, make_garment, garment_cm, ease, label):
    result = _region(_run(body, make_garment(shoulder=garment_cm)), Region.SHOULDER)
    assert result.ease_cm == pytest.approx(ease)
    assert result.label is label


def test_boundary_is_decided_on_exact_decimal_difference_not_float_subtraction(body, make_garment):
    """float 뺄셈은 경계 바로 아래 값이 나올 수 있다. 판정은 10진수 차이로 해야 한다."""
    assert 66.1 - 60.1 < 6  # float로 빼면 5.999999999999993이라 '밀착'으로 잘못 판정된다
    assert 74.1 - 60.1 < 14

    small_chest = body.model_copy(update={"chest_cm": 60.1})
    at_six = _region(_run(small_chest, make_garment(chest=66.1)), Region.CHEST)
    assert at_six.ease_cm == 6.0
    assert at_six.label is FitLabel.REGULAR

    at_fourteen = _region(_run(small_chest, make_garment(chest=74.1)), Region.CHEST)
    assert at_fourteen.ease_cm == 14.0
    assert at_fourteen.label is FitLabel.REGULAR


@pytest.mark.parametrize("waist_matched", [False])
def test_waist_with_unmatched_position_is_unknown_and_other_regions_are_kept(
    body, make_garment, waist_matched
):
    matched = _run(body, make_garment(waist_matched=True))
    unmatched = _run(body, make_garment(waist_matched=waist_matched))

    waist = _region(unmatched, Region.WAIST)
    assert waist.label is FitLabel.UNKNOWN
    assert waist.reason_code is ReasonCode.MEASUREMENT_NOT_COMPARABLE
    assert waist.ease_cm is None
    assert waist.garment_cm is None  # 대응이 확인되지 않은 값을 비교 가능한 값처럼 보이지 않는다

    for region in (Region.CHEST, Region.SHOULDER, Region.SLEEVE, Region.BODY_LENGTH):
        assert _region(unmatched, region) == _region(matched, region)
    assert "WAIST_NOT_EVALUATED" in unmatched.warnings
    assert "허리" not in unmatched.summary


def test_waist_null_is_unknown_with_missing_reason(body, make_garment):
    report = _run(body, make_garment(waist=None))
    waist = _region(report, Region.WAIST)
    assert waist.label is FitLabel.UNKNOWN
    assert waist.reason_code is ReasonCode.MEASUREMENT_MISSING
    assert waist.ease_cm is None
    assert _region(report, Region.CHEST).label is FitLabel.REGULAR


def test_same_input_and_rules_version_give_identical_results(body, make_garment):
    garment = make_garment()
    first = _run(body, garment)
    second = _run(body, garment)
    assert first == second
    assert first.model_dump_json() == second.model_dump_json()

    # created_at을 지정하지 않아도 수치와 판정은 같다.
    third = compute_report(body, garment, job_id="job_test")
    fourth = compute_report(body, garment, job_id="job_test")
    assert third.regions == fourth.regions
    assert third.summary == fourth.summary


def test_spec_example_report(body, make_garment):
    """명세서 4장 '리포트 예시와 예외' 표를 그대로 재현한다."""
    report = _run(body, make_garment())

    assert [r.region for r in report.regions] == [
        Region.CHEST,
        Region.SHOULDER,
        Region.WAIST,
        Region.SLEEVE,
        Region.BODY_LENGTH,
    ]
    chest, shoulder, waist, sleeve, length = report.regions

    assert (chest.body_cm, chest.garment_cm, chest.ease_cm) == (100.0, 108.0, 8.0)
    assert chest.label is FitLabel.REGULAR
    assert (shoulder.body_cm, shoulder.garment_cm, shoulder.ease_cm) == (44.0, 46.0, 2.0)
    assert shoulder.label is FitLabel.REGULAR
    assert (waist.body_cm, waist.garment_cm, waist.ease_cm) == (80.0, 96.0, 16.0)
    assert waist.label is FitLabel.LOOSE

    assert (sleeve.body_cm, sleeve.garment_cm, sleeve.ease_cm) == (60.0, 22.0, None)
    assert sleeve.label is FitLabel.NOT_EVALUATED
    assert "22.0 cm" in sleeve.explanation
    assert (length.body_cm, length.garment_cm, length.ease_cm) == (None, 70.0, None)
    assert length.label is FitLabel.NOT_EVALUATED
    assert "70.0 cm" in length.explanation

    assert chest.explanation.startswith(
        "가슴둘레가 몸보다 8.0 cm 커서 초기 기준상 기본 여유에 해당합니다"
    )
    assert report.summary == "가슴 기본 여유 · 어깨 기본 범위 · 허리 넉넉"
    assert report.rules_version == "fit_rules_v0"


def test_negative_ease_is_never_called_unwearable_and_there_is_no_single_score(body, make_garment):
    report = _run(body, make_garment(chest=98.0, waist=78.0))
    chest = _region(report, Region.CHEST)
    assert chest.label is FitLabel.INSUFFICIENT
    assert "착용 불가" not in chest.explanation
    assert "판단하지 않습니다" in chest.explanation
    assert "점수" not in report.summary and "%" not in report.summary


def test_report_keeps_the_user_input_values(body, make_garment):
    report = _run(body, make_garment())
    assert _region(report, Region.CHEST).body_cm == body.chest_cm
    assert _region(report, Region.SHOULDER).body_cm == body.shoulder_cm
    assert _region(report, Region.WAIST).body_cm == body.waist_cm


def test_demo_garment_is_flagged(body, make_garment):
    assert "DEMO_GARMENT" in _run(body, make_garment(is_measured=False)).warnings
    assert "DEMO_GARMENT" not in _run(body, make_garment(is_measured=True)).warnings


def test_rounding_is_applied_only_to_display_text(body, make_garment):
    # 여유량 5.96은 구간 판정에서는 6 미만(밀착)이고, 문구에서만 6.0으로 표시된다.
    body_small = body.model_copy(update={"chest_cm": 100.04})
    result = _region(_run(body_small, make_garment(chest=106.0)), Region.CHEST)
    assert result.ease_cm == pytest.approx(5.96)
    assert result.label is FitLabel.SNUG
    assert "5.96" not in result.explanation and "6.0 cm" in result.explanation


def test_largest_supported_garment_measurements_compute_without_error(body, make_garment):
    # 지원 범위의 상한 값도 판정과 문구 반올림에서 예외 없이 계산돼야 한다.
    # (범위 밖의 1e30은 입력 검증에서 거절하므로 여기까지 오지 않는다. test_validation 참고)
    top = MAX_GARMENT_CM
    report = _run(
        body, make_garment(chest=top, shoulder=top, waist=top, sleeve=top, length=top)
    )
    chest = _region(report, Region.CHEST)
    assert chest.label is FitLabel.LOOSE
    assert chest.ease_cm == pytest.approx(200.0)
    assert "200.0 cm" in chest.explanation
    shoulder = _region(report, Region.SHOULDER)
    assert shoulder.label is FitLabel.DROPPED
    assert "256.0 cm" in shoulder.explanation
    assert _region(report, Region.WAIST).label is FitLabel.LOOSE


# --- 실행 차단 -------------------------------------------------------------------------------


def test_garment_that_is_not_ready_is_blocked(body, make_garment):
    with pytest.raises(FitEngineError) as info:
        _run(body, make_garment(status=GarmentStatus.DRAFT))
    assert info.value.code is ErrorCode.GARMENT_NOT_READY


def test_missing_required_measurement_is_blocked(body, make_garment):
    # READY 모델은 필수 실측 없이 만들 수 없다. model_copy는 검증을 건너뛰므로,
    # 검증을 우회해 들어온 잘못된 데이터도 엔진이 한 번 더 막는지 확인한다.
    garment = make_garment()
    broken = garment.model_copy(
        update={"measurements": garment.measurements.model_copy(update={"shoulder_width_cm": None})}
    )
    with pytest.raises(FitEngineError) as info:
        _run(body, broken)
    assert info.value.code is ErrorCode.GARMENT_NOT_READY
    assert "shoulder_width_cm" in info.value.reason


def test_protocol_mismatch_is_blocked(body, make_garment):
    with pytest.raises(FitEngineError) as info:
        _run(body, make_garment(protocol="measure_v0"))
    assert info.value.code is ErrorCode.GARMENT_NOT_READY
    assert "프로토콜" in info.value.reason


def test_unsupported_garment_family_is_blocked(body, make_garment):
    with pytest.raises(FitEngineError):
        _run(body, make_garment(family="long_sleeve_hoodie"))


def test_port_error_does_not_leak_internal_reason(body, make_garment):
    with pytest.raises(FitEngineError) as info:
        _run(body, make_garment(protocol="measure_v0"))
    port_error = info.value.to_port_error()
    assert port_error.code is ErrorCode.GARMENT_NOT_READY
    assert port_error.retryable is False
    assert "measure_v0" not in port_error.safe_message

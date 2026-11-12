"""몸 맞추기 최적화(fitting.body.fit) 테스트. SMPL 없이 선형 합성 몸으로 시험한다."""

import json
import math

import pytest

np = pytest.importorskip("numpy")  # 선택 의존성 body가 없으면 건너뛴다
pytest.importorskip("scipy")

from fitting.body.fit import (  # noqa: E402
    BODY_TOLERANCE_CM,
    FIELDS,
    FitSettings,
    fit_body,
    judge,
)
from fitting.body.measure import BodyMeasurementsCm  # noqa: E402
from fitting.domain import Measurements  # noqa: E402

# 합성 몸: 치수 = 크기 × (평균 + J·β). 최적화 중 측정처럼 반올림하지 않는다(round_cm=False)
BASE = np.array([170.0, 95.0, 85.0, 38.0, 54.0])
J = np.array(
    [
        [4.0, 1.0, 0.0, 0.0],  # 키
        [1.0, 5.0, 1.0, 0.0],  # 가슴
        [0.0, 2.0, 5.0, 0.0],  # 허리
        [0.5, 0.5, 0.0, 1.0],  # 어깨
        [1.0, 0.0, 0.0, 0.5],  # 팔
    ]
)
SMALL = FitSettings(num_betas=4)
# 기준 신체 1 (docs/decisions.md "1주차 기준 [제안]" 1절)
BODY_1 = {
    "height_cm": 175.0,
    "chest_cm": 100.0,
    "waist_cm": 80.0,
    "shoulder_cm": 40.0,
    "arm_length_cm": 56.0,
}


def linear_measure(base=BASE, jacobian=J):
    calls = []

    def measure(betas, scale):
        calls.append((np.array(betas), scale))
        values = scale * (base + jacobian @ np.asarray(betas))
        return BodyMeasurementsCm(*(float(v) for v in values))

    measure.calls = calls
    return measure


def unreachable_arm():
    """팔이 형상 계수와 상관없고 크기만 따라가는 몸. 키와 팔을 같이 맞출 수 없다."""
    jacobian = J.copy()
    jacobian[4] = 0.0
    return linear_measure(jacobian=jacobian)


# --- 판정 --------------------------------------------------------------------------------------


def test_judge_passes_at_the_tolerance_and_fails_just_above():
    at_limit = BodyMeasurementsCm(176.0, 102.0, 78.0, 41.0, 57.5)  # 오차가 허용 오차와 같다
    assert judge(BODY_1, at_limit).passed

    above = BodyMeasurementsCm(176.1, 100.0, 80.0, 40.0, 54.4)  # 키 +1.1, 팔 −1.6
    result = judge(BODY_1, above)
    assert result.failed_fields == ("height_cm", "arm_length_cm")
    assert result.errors_cm["height_cm"] == pytest.approx(1.1)
    assert result.max_abs_error_cm == pytest.approx(1.6)


def test_judge_rounds_measurements_to_a_tenth_before_comparing():
    # 176.04 → 176.0 (오차 1.0, 통과), 176.06 → 176.1 (오차 1.1, 실패)
    assert judge(BODY_1, BodyMeasurementsCm(176.04, 100.0, 80.0, 40.0, 56.0)).passed
    assert not judge(BODY_1, BodyMeasurementsCm(176.06, 100.0, 80.0, 40.0, 56.0)).passed


def test_judge_fails_values_that_are_not_finite():
    result = judge(BODY_1, BodyMeasurementsCm(175.0, math.nan, 80.0, 40.0, 56.0))
    assert result.failed_fields == ("chest_cm",)


def test_tolerances_are_the_decision_values():
    # docs/decisions.md "1주차 기준 [제안]" 5절 = 명세서 5장
    assert dict(BODY_TOLERANCE_CM) == {
        "height_cm": 1.0,
        "chest_cm": 2.0,
        "waist_cm": 2.0,
        "shoulder_cm": 1.0,
        "arm_length_cm": 1.5,
    }
    assert FitSettings().num_starts == 3
    assert FitSettings().max_evals_per_start == 200


# --- 맞추기 ------------------------------------------------------------------------------------


def test_reachable_body_passes_and_stops_after_the_first_start():
    result = fit_body(BODY_1, linear_measure(), SMALL)

    assert result.passed
    assert result.stopped_by == "converged"
    assert len(result.starts) == 1  # 통과하면 남은 시작점을 돌리지 않는다
    assert result.chosen.judgement.max_abs_error_cm <= 1.0
    for name in FIELDS:
        assert abs(result.chosen.judgement.errors_cm[name]) <= BODY_TOLERANCE_CM[name]
    assert result.evaluations <= 1 + SMALL.max_evals_per_start


def test_unreachable_body_fails_after_all_starts_within_the_limit():
    result = fit_body(BODY_1 | {"arm_length_cm": 70.0}, unreachable_arm(), SMALL)

    assert not result.passed
    assert result.stopped_by == "exhausted"
    assert len(result.starts) == 3
    assert "arm_length_cm" in result.chosen.judgement.failed_fields
    assert all(start.evaluations <= SMALL.max_evals_per_start for start in result.starts)
    assert result.chosen.objective == min(start.objective for start in result.starts)


def test_evaluation_cap_is_a_hard_limit():
    settings = FitSettings(num_betas=4, max_evals_per_start=10)
    measure = unreachable_arm()
    result = fit_body(BODY_1 | {"arm_length_cm": 70.0}, measure, settings)

    assert [start.evaluations for start in result.starts] == [10, 10, 10]
    assert {start.stopped_by for start in result.starts} == {"max_evals"}
    assert result.evaluations == len(measure.calls) == 1 + 30


def test_time_limit_stops_the_search_and_fails():
    ticks = iter(range(10_000))
    result = fit_body(
        BODY_1 | {"arm_length_cm": 70.0},
        unreachable_arm(),
        FitSettings(num_betas=4, time_limit_s=20),
        clock=lambda: float(next(ticks)),
    )

    assert not result.passed
    assert result.stopped_by == "time_limit"
    assert result.starts[-1].stopped_by == "time_limit"


def test_time_limit_before_any_start_still_returns_a_failed_result():
    ticks = iter([0.0] + [100.0] * 100)
    result = fit_body(
        BODY_1,
        linear_measure(),
        FitSettings(num_betas=4, time_limit_s=1),
        clock=lambda: next(ticks),
    )

    assert not result.passed
    assert result.stopped_by == "time_limit"
    assert result.chosen.measurements is None


def test_unmeasurable_bodies_are_skipped_instead_of_crashing():
    inner = linear_measure()

    def measure(betas, scale):
        if betas[0] > 1.5:  # 이 범위에서는 단면이 비어 잴 수 없다고 친다
            raise ValueError("빈 단면")
        return inner(betas, scale)

    assert fit_body(BODY_1, measure, SMALL).passed


def test_accepts_the_measurements_model():
    body = Measurements(**BODY_1)
    assert fit_body(body, linear_measure(), SMALL).target_cm == BODY_1


def test_shape_prior_keeps_the_average_body_average():
    mean = Measurements(height_cm=170, chest_cm=95, waist_cm=85, shoulder_cm=38, arm_length_cm=54)
    result = fit_body(mean, linear_measure(), SMALL)

    assert result.passed
    assert np.abs(result.betas).max() < 0.2
    assert result.scale == pytest.approx(1.0, abs=0.01)


def test_initial_scale_follows_the_input_height():
    tall = BODY_1 | {"height_cm": 187.0}
    result = fit_body(tall, linear_measure(), SMALL)
    assert result.starts[0].initial_scale == pytest.approx(187.0 / 170.0)


def test_results_are_deterministic():
    target = BODY_1 | {"arm_length_cm": 70.0}
    first = fit_body(target, unreachable_arm(), SMALL)
    second = fit_body(target, unreachable_arm(), SMALL)
    assert first.chosen.betas == second.chosen.betas
    assert [s.initial_betas for s in first.starts] == [s.initial_betas for s in second.starts]


def test_record_is_strict_json_with_the_settings():
    result = fit_body(BODY_1 | {"arm_length_cm": 70.0}, unreachable_arm(), SMALL)
    record = json.loads(json.dumps(result.to_record(), allow_nan=False))

    assert record["passed"] is False
    assert record["target_cm"]["arm_length_cm"] == 70.0
    assert record["settings"]["max_evals_per_start"] == 200
    assert record["settings"]["tolerance_cm"] == dict(BODY_TOLERANCE_CM)
    assert len(record["starts"]) == 3
    assert set(record["starts"][0]) >= {"betas", "scale", "measured_cm", "error_cm", "evaluations"}

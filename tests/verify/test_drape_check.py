"""착장 점검 판정(fitting.verify.drape_check) 테스트. 메시 없이 면 넓이·부호 거리로 시험한다."""

import json
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from fitting.verify import (  # noqa: E402
    DRAPE_INVALID,
    FAIL,
    PASS,
    UNVERIFIABLE,
    DrapeCheckThresholds,
    judge_drape,
    summarize_penetration,
)

ROOT = Path(__file__).resolve().parents[2]
THRESHOLDS = DrapeCheckThresholds(penetration_depth_m=0.003, penetration_area_ratio_max=0.01)


def test_thresholds_come_from_the_drape_config():
    """기준값은 C2 착장 설정(명세서 6장 초기값)에서 읽는다."""
    for name in ("drape_v0.json", "drape_v1.json"):
        config = json.loads((ROOT / "config" / "drape" / name).read_text(encoding="utf-8"))
        thresholds = DrapeCheckThresholds.from_config(config, source=name)
        assert thresholds.penetration_depth_m == 0.003
        assert thresholds.penetration_area_ratio_max == 0.01
    with pytest.raises(ValueError, match="penetration_area_ratio_max"):
        DrapeCheckThresholds.from_config({"thresholds": {"penetration_depth_m": 0.003}})


def test_penetration_ratio_matches_the_c2_definition():
    """C2 `metrics.penetration`(Blender 쪽 판정)과 같은 정의라 같은 값이 나온다."""
    metrics = pytest.importorskip("fitting.adapters.blender.metrics")
    rng = np.random.default_rng(1)
    areas = rng.uniform(1e-5, 1e-4, 500)
    signed = rng.normal(0.002, 0.004, 500)
    signed[::50] = np.nan

    ours = summarize_penetration(areas, signed, 0.003)
    ratio, unknown, _ = metrics.penetration(areas, signed, 0.003, True)

    assert ours.area_ratio == pytest.approx(ratio)
    assert ours.unknown_faces == unknown == 10


def test_summary_values():
    areas = np.array([1.0, 1.0, 2.0, 4.0])
    signed = np.array([0.010, -0.001, -0.005, 0.002])  # 바깥, 얕게 안, 깊게 안, 바깥
    summary = summarize_penetration(areas, signed, 0.003)

    assert summary.area_ratio == pytest.approx(2.0 / 8.0)
    assert summary.inside_area_ratio == pytest.approx(3.0 / 8.0)
    assert summary.max_depth_m == pytest.approx(0.005)
    assert summary.penetrating_faces == 1
    assert summary.clearance_median_m == pytest.approx(0.006)
    with pytest.raises(ValueError):
        summarize_penetration(areas, signed[:3], 0.003)


def summary_with(ratio: float, unknown: int = 0):
    """관통 면적 비율이 ratio인 요약(넓이 1인 면 1000개)."""
    signed = np.full(1000, 0.01)
    signed[: int(round(ratio * 1000))] = -0.01
    if unknown:
        signed[-unknown:] = np.nan
    return summarize_penetration(np.ones(1000), signed, 0.003)


def test_judge_passes_at_the_limit_and_fails_above():
    at_limit = judge_drape(summary_with(0.01), THRESHOLDS, body_closed=True, garment_finite=True)
    assert at_limit.status == PASS and at_limit.passed and at_limit.error_code is None

    above = judge_drape(summary_with(0.011), THRESHOLDS, body_closed=True, garment_finite=True)
    assert above.status == FAIL and above.error_code == DRAPE_INVALID
    assert "1.10 %" in above.reasons[0] and "3 mm" in above.reasons[0]


def test_garment_with_nan_fails():
    result = judge_drape(summary_with(0.0), THRESHOLDS, body_closed=True, garment_finite=False)
    assert result.status == FAIL
    assert "NaN" in result.reasons[0]


def test_open_body_or_unknown_faces_are_unverifiable_not_passed():
    open_body = judge_drape(summary_with(0.0), THRESHOLDS, body_closed=False, garment_finite=True)
    assert open_body.status == UNVERIFIABLE
    assert open_body.error_code == DRAPE_INVALID  # 통과로 숨기지 않는다

    unknown = judge_drape(
        summary_with(0.0, unknown=3), THRESHOLDS, body_closed=True, garment_finite=True
    )
    assert unknown.status == UNVERIFIABLE
    assert "3개" in unknown.reasons[0]


def test_record_and_metrics_are_json():
    result = judge_drape(
        summary_with(0.02), THRESHOLDS, body_closed=True, garment_finite=True, details={"x": 1}
    )
    record = json.loads(json.dumps(result.to_record(), allow_nan=False))

    assert record["status"] == FAIL
    assert record["error_code"] == DRAPE_INVALID
    assert record["penetration"]["penetration_area_ratio"] == pytest.approx(0.02)
    assert record["thresholds"]["penetration_area_ratio_max"] == 0.01
    assert record["details"] == {"x": 1}
    assert result.metrics() == {
        "penetration_check_status": FAIL,
        "penetration_area_ratio": 0.02,
        "max_penetration_mm": 10.0,
    }

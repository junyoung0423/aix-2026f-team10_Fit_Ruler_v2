"""입력 검증: 범위 경계 포함, 문자열·NaN·음수·단위 혼용 거절 (명세서 3장)."""

import math

import pytest
from pydantic import ValidationError

from fitting.domain import (
    BodyInput,
    FittingRequest,
    GarmentMeasurements,
    GarmentStatus,
    GarmentVariant,
    MeasurementMeta,
    Measurements,
    Mode,
)
from fitting.domain.garment import MAX_GARMENT_CM

VALID = {
    "height_cm": 175.0,
    "chest_cm": 100.0,
    "waist_cm": 80.0,
    "shoulder_cm": 44.0,
    "arm_length_cm": 60.0,
}

RANGES = {
    "height_cm": (140.0, 210.0),
    "chest_cm": (60.0, 160.0),
    "waist_cm": (50.0, 160.0),
    "shoulder_cm": (28.0, 60.0),
    "arm_length_cm": (40.0, 85.0),
}


def test_valid_measurements_are_accepted():
    assert Measurements(**VALID).chest_cm == 100.0


@pytest.mark.parametrize("field", list(RANGES))
def test_range_edges_are_inclusive(field):
    low, high = RANGES[field]
    assert getattr(Measurements(**{**VALID, field: low}), field) == low
    assert getattr(Measurements(**{**VALID, field: high}), field) == high


@pytest.mark.parametrize("field", list(RANGES))
def test_values_just_outside_range_are_rejected(field):
    low, high = RANGES[field]
    for value in (low - 0.1, high + 0.1):
        with pytest.raises(ValidationError):
            Measurements(**{**VALID, field: value})


@pytest.mark.parametrize(
    "bad",
    ["175.0", "175 cm", True, None, float("nan"), float("inf"), float("-inf"), -175.0, 0.0],
    ids=["string", "string_with_unit", "bool", "none", "nan", "inf", "-inf", "negative", "zero"],
)
def test_invalid_values_are_rejected(bad):
    with pytest.raises(ValidationError):
        Measurements(**{**VALID, "height_cm": bad})


def test_integer_is_accepted_as_number():
    # JSON에서 175와 175.0은 같은 숫자다.
    assert Measurements(**{**VALID, "height_cm": 175}).height_cm == 175.0


def json_with_height(height_literal: str) -> str:
    """height_cm만 원하는 JSON 리터럴로 바꾼 요청 본문."""
    return (
        '{"height_cm": ' + height_literal + ", "
        '"chest_cm": 100, "waist_cm": 80, "shoulder_cm": 44, "arm_length_cm": 60}'
    )


def test_nan_in_json_is_rejected():
    raw = json_with_height("NaN")
    with pytest.raises(ValidationError):
        Measurements.model_validate_json(raw)


def test_string_number_in_json_is_rejected():
    raw = json_with_height('"175.0"')
    with pytest.raises(ValidationError):
        Measurements.model_validate_json(raw)


def test_missing_and_unknown_fields_are_rejected():
    missing = {k: v for k, v in VALID.items() if k != "waist_cm"}
    with pytest.raises(ValidationError):
        Measurements(**missing)
    with pytest.raises(ValidationError):
        Measurements(**VALID, height_in=68.9)


def test_unsupported_protocol_version_is_rejected():
    with pytest.raises(ValidationError):
        BodyInput(**VALID, measurement_protocol_version="measure_v0")


def test_fitting_request_defaults_to_full_mode_and_builds_body_input():
    request = FittingRequest(
        garment_variant_id="tee_regular_m_navy_v1",
        measurements=Measurements(**VALID),
        measurement_protocol_version="measure_v1",
    )
    assert request.mode is Mode.FULL
    body = request.to_body_input()
    assert body.chest_cm == 100.0
    assert body.measurement_protocol_version == "measure_v1"


def test_fitting_request_rejects_empty_garment_id_and_bad_mode():
    base = {
        "measurements": VALID,
        "measurement_protocol_version": "measure_v1",
    }
    with pytest.raises(ValidationError):
        FittingRequest.model_validate({**base, "garment_variant_id": ""})
    with pytest.raises(ValidationError):
        FittingRequest.model_validate({**base, "garment_variant_id": "x", "mode": "fast"})


def _garment(status, chest, shoulder, waist=None):
    return GarmentVariant(
        id="tee_x",
        style="regular",
        size="M",
        color="navy",
        fabric_preset="cotton_jersey_v0",
        status=status,
        measurements=GarmentMeasurements(
            chest_girth_cm=chest, shoulder_width_cm=shoulder, waist_girth_cm=waist
        ),
        measurement_meta=MeasurementMeta(
            measurement_protocol_version="measure_v1", source="테스트", is_measured=True
        ),
        asset_version="v1",
        source_ref="TEST",
    )


@pytest.mark.parametrize(("chest", "shoulder"), [(None, 46.0), (108.0, None), (None, None)])
def test_ready_garment_requires_chest_and_shoulder(chest, shoulder):
    with pytest.raises(ValidationError):
        _garment(GarmentStatus.READY, chest, shoulder)


def test_draft_garment_may_lack_required_measurements():
    assert _garment(GarmentStatus.DRAFT, None, None).status is GarmentStatus.DRAFT


GARMENT_FIELDS = [
    "chest_girth_cm",
    "waist_girth_cm",
    "shoulder_width_cm",
    "sleeve_length_cm",
    "body_length_cm",
]


@pytest.mark.parametrize("field", GARMENT_FIELDS)
def test_garment_measurement_upper_edge_is_inclusive(field):
    measurements = GarmentMeasurements(**{field: MAX_GARMENT_CM})
    assert getattr(measurements, field) == MAX_GARMENT_CM


@pytest.mark.parametrize("field", GARMENT_FIELDS)
@pytest.mark.parametrize(
    "bad", [MAX_GARMENT_CM + 0.1, 1e30, 1e308], ids=["just_over", "1e30", "1e308"]
)
def test_garment_measurement_above_supported_range_is_rejected(field, bad):
    # 1e30 cm는 예전에는 검증을 통과해 핏 계산의 반올림에서 decimal.InvalidOperation을 냈다.
    with pytest.raises(ValidationError):
        GarmentMeasurements(**{field: bad})


def test_garment_measurements_are_null_not_zero():
    # 필드가 없으면 0으로 채우지 않고 null로 둔다. 0과 음수는 실측으로 인정하지 않는다.
    assert _garment(GarmentStatus.READY, 108.0, 46.0).measurements.waist_girth_cm is None
    for bad in (0.0, -1.0, math.nan):
        with pytest.raises(ValidationError):
            _garment(GarmentStatus.READY, 108.0, 46.0, waist=bad)

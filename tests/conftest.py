from pathlib import Path

import pytest

from fitting.domain import (
    BodyInput,
    GarmentMeasurements,
    GarmentStatus,
    GarmentVariant,
    MeasurementMeta,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = REPO_ROOT / "docs" / "api" / "examples"


@pytest.fixture
def body() -> BodyInput:
    """명세서 4장 리포트 예시의 신체 치수."""
    return BodyInput(
        height_cm=175.0,
        chest_cm=100.0,
        waist_cm=80.0,
        shoulder_cm=44.0,
        arm_length_cm=60.0,
        measurement_protocol_version="measure_v1",
    )


@pytest.fixture
def make_garment():
    """기본값은 명세서 4장 리포트 예시의 의류. 필요한 값만 바꿔서 만든다."""

    def _make(
        *,
        chest: float | None = 108.0,
        shoulder: float | None = 46.0,
        waist: float | None = 96.0,
        sleeve: float | None = 22.0,
        length: float | None = 70.0,
        waist_matched: bool = True,
        status: GarmentStatus = GarmentStatus.READY,
        protocol: str = "measure_v1",
        family: str = "basic_short_sleeve_crew",
        is_measured: bool = True,
    ) -> GarmentVariant:
        return GarmentVariant(
            id="tee_test",
            style="regular",
            size="M",
            color="navy",
            fabric_preset="cotton_jersey_v0",
            garment_family=family,
            status=status,
            measurements=GarmentMeasurements(
                chest_girth_cm=chest,
                waist_girth_cm=waist,
                shoulder_width_cm=shoulder,
                sleeve_length_cm=sleeve,
                body_length_cm=length,
            ),
            measurement_meta=MeasurementMeta(
                measurement_protocol_version=protocol,
                source="테스트 데이터",
                is_measured=is_measured,
                waist_position_matched=waist_matched,
            ),
            asset_version="test_v1",
            source_ref="TEST",
        )

    return _make


@pytest.fixture(autouse=True)
def _fake_adapters(monkeypatch):
    """서버·실행 스크립트 테스트가 `default.yaml`이나 개발자 환경에 기대지 않게 한다.

    실제 어댑터가 머지돼 default가 바뀌거나 셸에 `FIT_ADAPTERS`가 있어도 기존 테스트는 전부 가짜로
    돈다. 어댑터 선택을 시험하는 테스트(`test_wiring.py` 등)는 이 값을 직접 바꾼다.
    """
    monkeypatch.setenv("FIT_ADAPTERS", "fake")


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch):
    """개발자의 저장소 `.env`가 테스트에 섞이지 않게 `.env` 읽기를 끈다(B6, `server/env_file.py`).

    하위 프로세스로 도구를 돌리는 테스트도 이 환경을 물려받는다. `.env` 읽기를 시험하는 테스트는
    파일 경로를 직접 넘긴다.
    """
    monkeypatch.setenv("FIT_ENV_FILE", "off")

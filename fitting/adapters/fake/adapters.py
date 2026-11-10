"""가짜 어댑터 구현. 산출물 ID는 `{job_id}_{이름}` 형식의 문자열이고 실제 파일은 만들지 않는다."""

from dataclasses import dataclass, field
from datetime import datetime

from fitting.domain import (
    SCHEMA_VERSION,
    BodyInput,
    ErrorCode,
    FitLabel,
    FitReport,
    GarmentVariant,
    QualityStatus,
    ReasonCode,
    Region,
    RegionResult,
    Stage,
)
from fitting.ports import (
    BODY_OUTPUTS,
    DRAPE_OUTPUTS,
    REALIZE_OUTPUTS,
    RENDER_OUTPUTS,
    VALIDATE_OUTPUTS,
    PortError,
    PortFailure,
    PortRequest,
    PortResult,
)
from fitting.services.fitting import FittingPorts

FAKE_TOOL_VERSIONS = {"fake": "0.1.0"}
FAKE_RULES_VERSION = "fake_rules_v0"


class FakeFitCalculator:
    """여유량(옷 - 몸)만 계산하고 판정은 하지 않는 가짜 핏 계산기. 실제 판정은 B2(#2)에서 넣는다."""

    def __init__(self) -> None:
        self.calls = 0

    def calculate(
        self,
        body: BodyInput,
        garment: GarmentVariant,
        *,
        job_id: str,
        created_at: datetime,
    ) -> FitReport:
        self.calls += 1
        m = garment.measurements
        regions = [
            _compared(Region.CHEST, body.chest_cm, m.chest_girth_cm),
            _compared(Region.SHOULDER, body.shoulder_cm, m.shoulder_width_cm),
            _compared(Region.WAIST, body.waist_cm, m.waist_girth_cm),
            RegionResult(
                region=Region.SLEEVE,
                body_cm=None,
                garment_cm=m.sleeve_length_cm,
                ease_cm=None,
                label=FitLabel.NOT_EVALUATED,
                reason_code=ReasonCode.NO_DIRECT_BODY_COUNTERPART,
                explanation="[가짜] 소매는 비교하지 않습니다.",
            ),
            RegionResult(
                region=Region.BODY_LENGTH,
                body_cm=None,
                garment_cm=m.body_length_cm,
                ease_cm=None,
                label=FitLabel.NOT_EVALUATED,
                reason_code=ReasonCode.BODY_INPUT_NOT_PROVIDED,
                explanation="[가짜] 기장은 비교하지 않습니다.",
            ),
        ]
        return FitReport(
            job_id=job_id,
            rules_version=FAKE_RULES_VERSION,
            regions=regions,
            summary="[가짜] 판정하지 않은 리포트",
            warnings=["가짜 핏 계산기 결과입니다. 판정은 #2 핏 계산기에서 합니다."],
            created_at=created_at,
        )


def _compared(region: Region, body_cm: float, garment_cm: float | None) -> RegionResult:
    if garment_cm is None:
        return RegionResult(
            region=region,
            body_cm=body_cm,
            garment_cm=None,
            ease_cm=None,
            label=FitLabel.UNKNOWN,
            reason_code=ReasonCode.MEASUREMENT_MISSING,
            explanation="[가짜] 의류 실측이 없어 비교하지 않습니다.",
        )
    return RegionResult(
        region=region,
        body_cm=body_cm,
        garment_cm=garment_cm,
        ease_cm=garment_cm - body_cm,
        label=FitLabel.UNKNOWN,
        explanation="[가짜] 여유량만 계산했고 판정은 하지 않았습니다.",
    )


@dataclass
class _FakeStep:
    """외부 기능 단계 하나를 흉내 낸다. 받은 요청은 `calls`에 남는다."""

    stage: Stage
    outputs: tuple[str, ...]
    fail_with: ErrorCode | None = None
    no_artifacts: bool = False
    calls: list[PortRequest] = field(default_factory=list)

    def _run(self, request: PortRequest, metrics: dict | None = None) -> PortResult:
        self.calls.append(request)
        if self.fail_with is not None:
            raise PortFailure(
                PortError(
                    code=self.fail_with,
                    stage=self.stage,
                    retryable=False,
                    safe_message="가짜 어댑터가 실패를 흉내 냈습니다.",
                    internal_details_ref=f"fake/{request.job_id}/{self.stage.value}",
                )
            )
        ids = [] if self.no_artifacts else [f"{request.job_id}_{name}" for name in self.outputs]
        return PortResult(
            schema_version=SCHEMA_VERSION,
            output_artifact_ids=ids,
            metrics=metrics or {},
            tool_versions=dict(FAKE_TOOL_VERSIONS),
        )


@dataclass
class FakeBodyBuilder(_FakeStep):
    stage: Stage = Stage.BODY_FIT
    outputs: tuple[str, ...] = BODY_OUTPUTS

    def build_body(self, request: PortRequest, body: BodyInput) -> PortResult:
        """다시 잰 치수는 입력 그대로, 오차는 0인 가짜 지표."""
        return self._run(
            request,
            {
                "fitted_height_cm": body.height_cm,
                "fitted_chest_cm": body.chest_cm,
                "fitted_waist_cm": body.waist_cm,
                "fitted_shoulder_cm": body.shoulder_cm,
                "fitted_arm_length_cm": body.arm_length_cm,
                "max_abs_error_cm": 0.0,
            },
        )


@dataclass
class FakeDraper(_FakeStep):
    stage: Stage = Stage.DRAPE
    outputs: tuple[str, ...] = DRAPE_OUTPUTS

    def drape(self, request: PortRequest, garment: GarmentVariant) -> PortResult:
        return self._run(request, {"penetration_ratio": 0.0})


@dataclass
class FakeRenderer(_FakeStep):
    stage: Stage = Stage.RENDER
    outputs: tuple[str, ...] = RENDER_OUTPUTS

    def render(self, request: PortRequest, garment: GarmentVariant) -> PortResult:
        return self._run(request, {"frames": 48, "fps": 24})


@dataclass
class FakeRealizer(_FakeStep):
    stage: Stage = Stage.REALIZE
    outputs: tuple[str, ...] = REALIZE_OUTPUTS

    def realize(self, request: PortRequest, garment: GarmentVariant) -> PortResult:
        """가짜 외부 요청 ID와 0 비용·시간 지표."""
        return self._run(
            request,
            {
                "external_request_id": f"fake_{request.job_id}",
                "cost_usd": 0.0,
                "queue_s": 0.0,
                "processing_s": 0.0,
            },
        )


@dataclass
class FakeValidator(_FakeStep):
    """`quality`로 자동 검증 판정(PASS·FAIL·UNVERIFIABLE)을 정한다.

    산출물은 [대표 프레임, 대응 렌더 프레임], 지표는 판정과 숫자 지표 두 개다. 숫자는 가짜 값이다.
    """

    stage: Stage = Stage.VALIDATE
    outputs: tuple[str, ...] = VALIDATE_OUTPUTS
    quality: QualityStatus = QualityStatus.PASS
    color_delta_e: float = 1.5
    silhouette_iou: float = 0.95

    def validate(self, request: PortRequest, garment: GarmentVariant) -> PortResult:
        return self._run(
            request,
            {
                "quality_status": self.quality.value,
                "color_delta_e": self.color_delta_e,
                "silhouette_iou": self.silhouette_iou,
            },
        )


def fake_ports(**overrides) -> FittingPorts:
    """모두 가짜인 포트 묶음. 바꿀 것만 키워드로 넘긴다.

    예: `fake_ports(drape=FakeDraper(fail_with=ErrorCode.DRAPE_INVALID))`
    """
    ports = {
        "fit": FakeFitCalculator(),
        "body": FakeBodyBuilder(),
        "drape": FakeDraper(),
        "render": FakeRenderer(),
        "realize": FakeRealizer(),
        "validate": FakeValidator(),
    }
    unknown = set(overrides) - set(ports)
    if unknown:
        raise TypeError(f"모르는 포트: {sorted(unknown)}")
    ports.update(overrides)
    return FittingPorts(**ports)

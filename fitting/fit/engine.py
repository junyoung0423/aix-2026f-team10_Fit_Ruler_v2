"""수치 기반 핏 리포트 계산 (#2, 명세서 4장).

이전 저장소 `fit_ruler.fit_engine.engine`(정민 작성)을 옮겼다. 판정 결과는 이전과 같다.

영상 경로와 독립이며 입력 치수와 의류 실측의 규칙 기반 비교만 한다. 실사 영상의 외형으로
수치나 판정을 고치지 않고, 사용자가 입력한 치수를 그대로 쓴다.
"""

from datetime import datetime, timezone
from decimal import Decimal

from fitting.domain.enums import (
    ErrorCode,
    FitLabel,
    GarmentStatus,
    ReasonCode,
    Region,
    Stage,
)
from fitting.domain.garment import GarmentVariant
from fitting.domain.measurements import BodyInput
from fitting.domain.report import FitReport, RegionResult
from fitting.fit.loader import default_fit_rules
from fitting.fit.rules import BodyLengthEstimate, FitRules, RegionRule, display_cm
from fitting.ports.dto import PortError

_GARMENT_NOT_READY_MESSAGE = "선택한 의류를 아직 사용할 수 없어요. 다른 의류를 선택해 주세요."


class FitEngineError(Exception):
    """핏 계산을 시작할 수 없는 의류·입력 조합. 화면에는 `to_port_error()`만 보여준다."""

    def __init__(self, code: ErrorCode, reason: str) -> None:
        super().__init__(f"{code.value}: {reason}")
        self.code = code
        self.reason = reason  # 운영 기록용. 화면에 보이지 않는다

    def to_port_error(self) -> PortError:
        return PortError(
            code=self.code,
            stage=Stage.FIT_CALCULATE,
            retryable=False,
            safe_message=_GARMENT_NOT_READY_MESSAGE,
        )


def to_decimal(value: float) -> Decimal:
    """float를 10진수로 바꾼다. 사람이 입력한 자릿수 그대로(repr) 바꿔 이진 오차를 피한다.

    예: 106.1 - 100.1은 float로는 5.999999999999986이지만 10진수로는 정확히 6.0이다.
    """
    return Decimal(repr(value))


def _check_computable(body: BodyInput, garment: GarmentVariant, rules: FitRules) -> None:
    """필수 실측 누락, 프로토콜 불일치, 준비되지 않은 의류는 실행을 차단한다 (명세서 4장)."""
    if garment.status is not GarmentStatus.READY:
        raise FitEngineError(
            ErrorCode.GARMENT_NOT_READY, f"의류 상태가 {garment.status.value}입니다"
        )
    if garment.garment_family not in rules.applicable_families:
        raise FitEngineError(
            ErrorCode.GARMENT_NOT_READY,
            f"{rules.version}이 적용되지 않는 의류군입니다: {garment.garment_family}",
        )
    garment_protocol = garment.measurement_meta.measurement_protocol_version
    if garment_protocol != body.measurement_protocol_version:
        raise FitEngineError(
            ErrorCode.GARMENT_NOT_READY,
            f"측정 프로토콜이 다릅니다: 신체 {body.measurement_protocol_version}, "
            f"의류 {garment_protocol}",
        )
    missing = [
        name
        for name, value in (
            ("chest_girth_cm", garment.measurements.chest_girth_cm),
            ("shoulder_width_cm", garment.measurements.shoulder_width_cm),
        )
        if value is None
    ]
    if missing:
        raise FitEngineError(
            ErrorCode.GARMENT_NOT_READY, f"필수 의류 실측이 없습니다: {', '.join(missing)}"
        )


def _compare(rule: RegionRule, body_cm: float, garment_cm: float) -> RegionResult:
    """여유량(의류 - 신체)을 반올림 없이 계산해 구간에 대응시킨다."""
    ease = to_decimal(garment_cm) - to_decimal(body_cm)
    band = rule.classify(ease)
    return RegionResult(
        region=rule.region,
        body_cm=body_cm,
        garment_cm=garment_cm,
        ease_cm=float(ease),
        label=band.label,
        explanation=rule.explain(ease, band),
    )


def _waist_result(rule: RegionRule, body: BodyInput, garment: GarmentVariant) -> RegionResult:
    """허리는 위치 대응이 확인된 의류만 비교한다. 아니면 UNKNOWN이고 다른 부위는 유지한다.

    위치 대응이 확인되지 않은 의류 둘레(garment_cm)는 비교 가능한 값처럼 보이지 않도록
    null로 둔다.
    """
    waist_girth = garment.measurements.waist_girth_cm
    if waist_girth is None:
        return RegionResult(
            region=Region.WAIST,
            body_cm=body.waist_cm,
            garment_cm=None,
            ease_cm=None,
            label=FitLabel.UNKNOWN,
            reason_code=ReasonCode.MEASUREMENT_MISSING,
            explanation="의류 허리 실측이 없어 허리 판정을 하지 않습니다.",
        )
    if not garment.measurement_meta.waist_position_matched:
        return RegionResult(
            region=Region.WAIST,
            body_cm=body.waist_cm,
            garment_cm=None,
            ease_cm=None,
            label=FitLabel.UNKNOWN,
            reason_code=ReasonCode.MEASUREMENT_NOT_COMPARABLE,
            explanation=(
                "의류 허리 측정 위치가 신체 자연 허리와 대응하는지 확인되지 않아 "
                "허리 판정을 하지 않습니다."
            ),
        )
    return _compare(rule, body.waist_cm, waist_girth)


def _sleeve_result(body: BodyInput, garment: GarmentVariant) -> RegionResult:
    """소매 길이는 사람의 전체 팔길이와 직접 차감하지 않는다. 의류 실측만 보여준다."""
    sleeve = garment.measurements.sleeve_length_cm
    if sleeve is None:
        return RegionResult(
            region=Region.SLEEVE,
            body_cm=body.arm_length_cm,
            garment_cm=None,
            ease_cm=None,
            label=FitLabel.NOT_EVALUATED,
            reason_code=ReasonCode.MEASUREMENT_MISSING,
            explanation="의류 소매 실측이 없어 표시할 수 없습니다.",
        )
    return RegionResult(
        region=Region.SLEEVE,
        body_cm=body.arm_length_cm,
        garment_cm=sleeve,
        ease_cm=None,
        label=FitLabel.NOT_EVALUATED,
        reason_code=ReasonCode.NO_DIRECT_BODY_COUNTERPART,
        explanation=(
            f"소매 실측 {sleeve:.1f} cm입니다. 사람의 전체 팔길이와 직접 비교하지 않아 "
            "적정 여부는 판단하지 않습니다."
        ),
    )


def _body_length_result(
    body: BodyInput, garment: GarmentVariant, estimate: BodyLengthEstimate
) -> RegionResult:
    """몸통 길이 입력이 없으므로 적정 기장을 판정하지 않고 의류 실측만 보여준다.

    기준표의 `body_length_estimate`가 켜져 있으면 키로 추정한 몸통 기준 길이와의 차이를
    설명에 더하고 `is_estimated=True`로 표시한다. 추정값이므로 이때도 판정은 하지 않는다.
    """
    length = garment.measurements.body_length_cm
    if length is not None and estimate.enabled:
        return _estimated_body_length_result(body, length, estimate)
    if length is None:
        return RegionResult(
            region=Region.BODY_LENGTH,
            body_cm=None,
            garment_cm=None,
            ease_cm=None,
            label=FitLabel.NOT_EVALUATED,
            reason_code=ReasonCode.MEASUREMENT_MISSING,
            explanation="의류 기장 실측이 없어 표시할 수 없습니다.",
        )
    return RegionResult(
        region=Region.BODY_LENGTH,
        body_cm=None,
        garment_cm=length,
        ease_cm=None,
        label=FitLabel.NOT_EVALUATED,
        reason_code=ReasonCode.BODY_INPUT_NOT_PROVIDED,
        explanation=(
            f"기장 실측 {length:.1f} cm입니다. 몸통 길이 입력이 없어 적정 기장은 판단하지 않습니다."
        ),
    )


def _estimated_body_length_result(
    body: BodyInput, length: float, estimate: BodyLengthEstimate
) -> RegionResult:
    """키 × 비율로 추정한 몸통 기준 길이와 의류 기장의 차이를 설명한다. 판정은 하지 않는다."""
    assert estimate.method == "height_ratio" and estimate.ratio is not None  # 로더가 보장한다
    estimated = to_decimal(body.height_cm) * estimate.ratio
    diff = to_decimal(length) - estimated
    shown = display_cm(abs(diff))
    if diff == 0:
        relation = "와 같습니다"
    elif shown == "0.0":
        relation = "와 거의 같습니다"
    elif diff > 0:
        relation = f"보다 {shown} cm 깁니다"
    else:
        relation = f"보다 {shown} cm 짧습니다"
    return RegionResult(
        region=Region.BODY_LENGTH,
        body_cm=float(estimated),
        garment_cm=length,
        ease_cm=float(diff),
        label=FitLabel.NOT_EVALUATED,
        reason_code=ReasonCode.BODY_INPUT_NOT_PROVIDED,
        is_estimated=True,
        explanation=(
            f"기장 실측 {length:.1f} cm는 키로 추정한 몸통 기준 길이 "
            f"{display_cm(estimated)} cm{relation}. 몸통 길이 입력이 없어 추정한 값이므로 "
            "적정 기장은 판단하지 않습니다."
        ),
    )


def compute_report(
    body: BodyInput,
    garment: GarmentVariant,
    *,
    job_id: str,
    created_at: datetime | None = None,
    rules: FitRules | None = None,
) -> FitReport:
    """신체 입력과 의류 실측으로 핏 리포트를 만든다. `rules`가 없으면 기본 기준표를 쓴다.

    같은 입력과 같은 규칙 버전이면 `created_at`을 제외한 모든 값이 같다.

    Raises:
        FitEngineError: 의류가 READY가 아니거나 필수 실측·프로토콜이 맞지 않아 계산할 수 없다.
    """
    rules = rules or default_fit_rules()
    _check_computable(body, garment, rules)

    chest_cm = garment.measurements.chest_girth_cm
    shoulder_cm = garment.measurements.shoulder_width_cm
    assert chest_cm is not None and shoulder_cm is not None  # _check_computable이 보장한다

    chest = _compare(rules.chest, body.chest_cm, chest_cm)
    shoulder = _compare(rules.shoulder, body.shoulder_cm, shoulder_cm)
    waist = _waist_result(rules.waist, body, garment)
    regions = [
        chest,
        shoulder,
        waist,
        _sleeve_result(body, garment),
        _body_length_result(body, garment, rules.body_length_estimate),
    ]

    # 종합 문구는 판정한 부위만 나열한다. 단일 적합 점수나 확률은 만들지 않는다.
    summary_parts = [
        f"{rule.short_ko} {_label_ko(rule, result)}"
        for rule, result in (
            (rules.chest, chest),
            (rules.shoulder, shoulder),
            (rules.waist, waist),
        )
        if result.label is not FitLabel.UNKNOWN
    ]

    warnings: list[str] = []
    if waist.label is FitLabel.UNKNOWN:
        warnings.append("WAIST_NOT_EVALUATED")
    if garment.is_demo:
        warnings.append("DEMO_GARMENT")

    return FitReport(
        job_id=job_id,
        rules_version=rules.version,
        regions=regions,
        summary=" · ".join(summary_parts),
        warnings=warnings,
        created_at=created_at or datetime.now(timezone.utc),
    )


def _label_ko(rule: RegionRule, result: RegionResult) -> str:
    for band in rule.bands:
        if band.label is result.label:
            return band.label_ko
    raise ValueError(f"{rule.region.value}에 없는 판정입니다: {result.label.value}")


class RuleFitCalculator:
    """`FitCalculator` 포트의 실제 구현 (#2). 기준표 하나로 리포트를 만든다.

    3D·AI 없이 신체 입력과 의류 실측만으로 계산한다. 계산할 수 없는 의류면 `FitEngineError`를
    던지고, 피팅 흐름(`run_fitting`)과 핏 API가 이를 GARMENT_NOT_READY로 바꾼다.
    """

    def __init__(self, rules: FitRules | None = None) -> None:
        self.rules = rules or default_fit_rules()

    def calculate(
        self,
        body: BodyInput,
        garment: GarmentVariant,
        *,
        job_id: str,
        created_at: datetime,
    ) -> FitReport:
        return compute_report(body, garment, job_id=job_id, created_at=created_at, rules=self.rules)

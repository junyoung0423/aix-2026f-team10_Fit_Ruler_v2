"""착장 점검 판정 (D4, #8). numpy만 쓰고 외부 SDK·어댑터를 import하지 않는다.

착장 결과(옷 메시)가 몸을 파고들었는지를 기준으로 판정한다. 명세서 6장 착장 검사 중 "충돌"
항목이고, 기준값은 C2의 착장 설정(`config/drape/drape_v*.json`의 `thresholds`)을 그대로 쓴다.

- 관통: 옷 면 중심의 몸 표면 부호 거리(바깥 +)가 `-penetration_depth_m`보다 깊은 면의 넓이 비율.
  C2 `fitting.adapters.blender.metrics.penetration`과 같은 정의다(테스트로 같은 값인지 확인한다).
- 판정
  - PASS: 관통 면적 비율 ≤ `penetration_area_ratio_max`, 옷 메시 값이 모두 유한
  - FAIL: 관통 면적 비율 초과, 또는 옷 메시에 NaN·inf가 있다 → `DRAPE_INVALID`
  - UNVERIFIABLE: 몸 메시가 닫혀 있지 않거나 부호 거리를 못 구한 면이 있다(명세서 6장 "검증 불가")
    → 실패로 본다(`DRAPE_INVALID`). 통과로 숨기지 않는다.

부호 거리 계산(메시 기하)은 `fitting/adapters/meshcheck/`가 하고, 이 모듈은 그 결과로 판정만 한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np

CHECK_VERSION = "drape_check_v1"

PASS, FAIL, UNVERIFIABLE = "PASS", "FAIL", "UNVERIFIABLE"
DRAPE_INVALID = "DRAPE_INVALID"


@dataclass(frozen=True)
class DrapeCheckThresholds:
    """착장 점검 기준. 값은 착장 설정 파일의 `thresholds`에서 읽는다(여기에 수치를 두지 않는다)."""

    penetration_depth_m: float  # 이보다 깊이 들어간 면만 관통으로 센다(명세서 6장 초기값 3 mm)
    penetration_area_ratio_max: float  # 관통 면적 비율 상한(명세서 6장 초기값 1 %)
    source: str = ""  # 기준을 읽은 설정 이름과 버전(기록용)

    @classmethod
    def from_config(cls, config: Mapping[str, Any], source: str = "") -> DrapeCheckThresholds:
        """착장 설정 dict(`config/drape/drape_v*.json` 전체 또는 `thresholds` 블록)에서 읽는다.

        Raises:
            ValueError: 필요한 값이 없다.
        """
        thresholds = config.get("thresholds", config)
        missing = [
            key
            for key in ("penetration_depth_m", "penetration_area_ratio_max")
            if key not in thresholds
        ]
        if missing:
            raise ValueError(f"착장 설정에 기준값이 없습니다: {', '.join(missing)}")
        return cls(
            penetration_depth_m=float(thresholds["penetration_depth_m"]),
            penetration_area_ratio_max=float(thresholds["penetration_area_ratio_max"]),
            source=source,
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "penetration_depth_m": self.penetration_depth_m,
            "penetration_area_ratio_max": self.penetration_area_ratio_max,
            "source": self.source,
        }


@dataclass(frozen=True)
class PenetrationSummary:
    """면 중심 부호 거리로 낸 관통 지표."""

    area_ratio: float  # depth보다 깊은 면적 비율 (C2 penetration_area_ratio와 같은 정의)
    inside_area_ratio: float  # 깊이와 상관없이 몸 안에 있는 면적 비율(진단용)
    max_depth_m: float  # 가장 깊이 들어간 깊이(몸 안이면 양수, 없으면 0)
    penetrating_faces: int  # depth보다 깊은 면 수
    unknown_faces: int  # 부호 거리를 못 구한 면 수(NaN)
    total_faces: int
    clearance_median_m: float | None  # 몸 밖에 있는 면의 표면까지 거리 중앙값(진단용)

    def to_record(self) -> dict[str, Any]:
        return {
            "penetration_area_ratio": round(self.area_ratio, 6),
            "inside_area_ratio": round(self.inside_area_ratio, 6),
            "max_penetration_mm": round(self.max_depth_m * 1000, 2),
            "penetrating_faces": self.penetrating_faces,
            "unknown_faces": self.unknown_faces,
            "total_faces": self.total_faces,
            "clearance_median_mm": None
            if self.clearance_median_m is None
            else round(self.clearance_median_m * 1000, 2),
        }


def summarize_penetration(
    areas: np.ndarray, signed_dist: np.ndarray, depth_m: float
) -> PenetrationSummary:
    """면 넓이와 면 중심 부호 거리(바깥 +, 못 구하면 NaN)로 관통 지표를 낸다."""
    areas = np.asarray(areas, dtype=float)
    signed = np.asarray(signed_dist, dtype=float)
    if areas.shape != signed.shape:
        raise ValueError("면 넓이와 부호 거리의 개수가 다릅니다.")
    known = np.isfinite(signed)
    filled = np.where(known, signed, 0.0)
    total = float(areas.sum())
    deep = filled < -depth_m
    inside = filled < 0.0
    outside = known & (signed >= 0.0)
    return PenetrationSummary(
        area_ratio=float(areas[deep].sum() / total) if total > 0 else 0.0,
        inside_area_ratio=float(areas[inside].sum() / total) if total > 0 else 0.0,
        max_depth_m=float(max(0.0, -filled.min())) if filled.size else 0.0,
        penetrating_faces=int(deep.sum()),
        unknown_faces=int((~known).sum()),
        total_faces=int(signed.size),
        clearance_median_m=float(np.median(signed[outside])) if outside.any() else None,
    )


@dataclass(frozen=True)
class DrapeCheckResult:
    status: str  # PASS · FAIL · UNVERIFIABLE
    penetration: PenetrationSummary
    thresholds: DrapeCheckThresholds
    body_closed: bool
    garment_finite: bool
    reasons: tuple[str, ...] = ()  # 통과하지 못한 이유(한국어, 기록·로그용)
    details: dict[str, Any] = field(default_factory=dict)  # 메시 크기·가장 깊은 곳 등(진단용)

    @property
    def passed(self) -> bool:
        return self.status == PASS

    @property
    def error_code(self) -> str | None:
        return None if self.passed else DRAPE_INVALID

    def metrics(self) -> dict[str, float | int | str]:
        """포트 지표(`PortResult.metrics`)에 넣을 수 있는 값.

        **관통만** 본 판정이다(신장·안정·봉제선은 보지 않는다). 그래서 착장 검사 전체 통과와
        헷갈리지 않게 `penetration_check_status`라고 부른다.
        """
        return {
            "penetration_check_status": self.status,
            "penetration_area_ratio": round(self.penetration.area_ratio, 6),
            "max_penetration_mm": round(self.penetration.max_depth_m * 1000, 2),
        }

    def to_record(self) -> dict[str, Any]:
        return {
            "check_version": CHECK_VERSION,
            "status": self.status,
            "passed": self.passed,
            "error_code": self.error_code,
            "reasons": list(self.reasons),
            "body_closed": self.body_closed,
            "garment_finite": self.garment_finite,
            "penetration": self.penetration.to_record(),
            "thresholds": self.thresholds.to_record(),
            "details": self.details,
        }


def judge_drape(
    penetration: PenetrationSummary,
    thresholds: DrapeCheckThresholds,
    *,
    body_closed: bool,
    garment_finite: bool,
    details: Mapping[str, Any] | None = None,
) -> DrapeCheckResult:
    """관통 지표를 기준과 비교해 판정한다."""
    reasons: list[str] = []
    if not garment_finite:
        reasons.append("옷 메시에 유한하지 않은 좌표(NaN·inf)가 있습니다.")
    depth_mm = thresholds.penetration_depth_m * 1000
    if penetration.area_ratio > thresholds.penetration_area_ratio_max:
        reasons.append(
            f"몸을 {depth_mm:g} mm보다 깊이 파고든 옷 면적이 "
            f"{penetration.area_ratio * 100:.2f} %로 기준 "
            f"{thresholds.penetration_area_ratio_max * 100:g} %를 넘었습니다."
        )
    unverifiable: list[str] = []
    if not body_closed:
        unverifiable.append("몸 메시가 닫혀 있지 않아 안팎을 가릴 수 없습니다.")
    if penetration.unknown_faces:
        unverifiable.append(
            f"부호 거리를 구하지 못한 옷 면이 {penetration.unknown_faces}개 있습니다."
        )

    if reasons:
        status = FAIL
    elif unverifiable:
        status = UNVERIFIABLE
    else:
        status = PASS
    return DrapeCheckResult(
        status=status,
        penetration=penetration,
        thresholds=thresholds,
        body_closed=body_closed,
        garment_finite=garment_finite,
        reasons=tuple(reasons + unverifiable),
        details=dict(details or {}),
    )

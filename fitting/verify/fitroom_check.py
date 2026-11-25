"""피팅룸 영상 검증 판정 (#11). numpy만 쓰고 외부 SDK·어댑터를 import하지 않는다.

피팅룸 실사화(`fal_fitroom`)는 사람이 제자리에서 한 바퀴 도는 영상이라, 렌더와 실사 프레임을 같은
자리에서
겹쳐 보는 11프레임 규칙(`visual_check`)을 영상에 그대로 쓸 수 없다. 그래서 둘로 나눠 본다.

1. **핏 확인 사진**: 렌더와 실사화 사진(렌더와 같은 구도·체형·핏으로 만든 실사 사진)을 명세서
   7장
   프레임 기준(`visual_check.judge_frame`: 면적 0.5~1.5배, IoU ≥ 0.85, 가슴·밑단 폭 ±5 %,
   CIEDE2000 ≤ 8)으로 한 장 비교한다.
2. **영상**: 검은 화면이 아닌지, 정면으로 돌아와 끝나는지(첫·끝 프레임 차이), 1초 간격 시점마다 첫
   프레임의 옷 색을 찾을 수 있는지, 시간에 따라 옷 밝기가 크게 흔들리지 않는지(L* 최대 − 최소).
   기준값은 실험 9편에서 골랐다(`config/validate/fitroom_check_v1.json`, docs/realize-fitroom.md).

판정
- FAIL: 핏 확인 사진이 실패, 검은 영상, 끝 프레임 차이 초과, 옷 색을 못 찾은 시점이 있음, 밝기
  흔들림 초과 중 하나라도 있다
- UNVERIFIABLE: 실패는 없지만 핏 확인 사진을 잴 수 없다(옷 영역을 못 찾음 등)
- PASS: 그 밖

옆·뒤 핏은 렌더가 정면 한 장뿐이라 자동으로 비교하지 않는다. 얼굴·중대한 변형도 자동으로 찾지
않는다.
그래서 PASS에도 사람 확인 표시(`human_review_required`)를 기록에 남긴다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .visual_check import FAIL, PASS, UNVERIFIABLE, FrameResult

CHECK_VERSION = "fitroom_check_v1"


@dataclass(frozen=True)
class FitRoomVideoCriteria:
    sample_step_s: float
    end_frame_gap_max: float  # 첫·끝 프레임 평균 차이 상한(0~255, 96×128로 줄여 비교)
    lightness_range_max: float  # 1초 간격 옷 밝기(L*) 최대 − 최소 상한
    garment_color_distance: float  # 첫 프레임 몸통 색과 이 거리 안의 픽셀을 옷으로 본다
    lightness_weight: float  # 거리에서 L* 차이에 곱하는 값(2.55 = OpenCV 8비트 Lab 거리와 같음)
    garment_min_fraction: float  # 옷 픽셀이 화면의 이 비율 이상이면 그 시점에 옷을 찾음
    black_mean_max: float
    black_std_max: float
    source: str = ""

    @classmethod
    def from_config(cls, config: Mapping[str, Any], source: str = "") -> FitRoomVideoCriteria:
        """Raises: ValueError — `video` 블록이나 값이 없거나 범위 밖이다."""
        video = config.get("video")
        if not isinstance(video, Mapping):
            raise ValueError("피팅룸 검증 설정에 video 블록이 없습니다")
        keys = (
            "sample_step_s",
            "end_frame_gap_max",
            "lightness_range_max",
            "garment_color_distance",
            "lightness_weight",
            "garment_min_fraction",
            "black_mean_max",
            "black_std_max",
        )
        missing = [k for k in keys if k not in video]
        if missing:
            raise ValueError(f"피팅룸 검증 설정에 값이 없습니다: {', '.join(missing)}")
        criteria = cls(**{k: float(video[k]) for k in keys}, source=source)
        if any(getattr(criteria, k) <= 0 for k in keys):
            raise ValueError("피팅룸 검증 설정 값은 모두 0보다 커야 합니다")
        return criteria

    def to_record(self) -> dict[str, Any]:
        return {
            "sample_step_s": self.sample_step_s,
            "end_frame_gap_max": self.end_frame_gap_max,
            "lightness_range_max": self.lightness_range_max,
            "garment_color_distance": self.garment_color_distance,
            "lightness_weight": self.lightness_weight,
            "garment_min_fraction": self.garment_min_fraction,
            "black_mean_max": self.black_mean_max,
            "black_std_max": self.black_std_max,
            "source": self.source,
        }


@dataclass(frozen=True)
class VideoMeasure:
    """영상 측정값(기록 자릿수로 반올림해 두고 그 값으로 판정한다)."""

    black: bool
    end_frame_gap: float | None  # 첫·끝 프레임 차이
    lightness: tuple[float, ...]  # 1초 간격 옷 밝기(L*), 옷을 못 찾은 시점은 빠진다
    seconds: float
    size: tuple[int, int]
    garment_missing: int = 0  # 옷 색을 찾지 못한 시점 수(검은 영상이면 0)

    @property
    def lightness_range(self) -> float | None:
        return round(max(self.lightness) - min(self.lightness), 1) if self.lightness else None

    def to_record(self) -> dict[str, Any]:
        return {
            "black": self.black,
            "end_frame_gap": self.end_frame_gap,
            "lightness_per_sample": list(self.lightness),
            "lightness_range": self.lightness_range,
            "garment_missing_samples": self.garment_missing,
            "seconds": self.seconds,
            "size": list(self.size),
        }


@dataclass(frozen=True)
class FitRoomCheckResult:
    status: str
    fit: FrameResult | None  # 핏 확인 사진 판정(없으면 None)
    video: VideoMeasure
    criteria: FitRoomVideoCriteria
    fails: tuple[str, ...] = ()
    unverifiable: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def human_review_required(self) -> bool:
        return self.status == PASS

    def reasons(self) -> list[str]:
        text = {
            "fit_frame": "핏 확인 사진이 렌더와 다름(명세서 7장 프레임 기준)",
            "black": "영상이 검은 화면",
            "end_frame_gap": "정면으로 돌아와 끝나지 않음(첫·끝 프레임 차이 초과)",
            "garment_missing": "옷 색을 찾지 못한 시점이 있음(옷 색이 크게 바뀌었거나 화면 밖)",
            "lightness_range": "시간에 따라 옷 밝기가 크게 흔들림",
            "fit_frame_unmeasurable": "핏 확인 사진을 잴 수 없음",
            "no_fit_frame": "핏 확인 사진이 없음",
        }
        return [text[c] for c in (*self.fails, *self.unverifiable)]

    def metrics(self) -> dict[str, float | int | str]:
        """포트 지표. 숫자는 화면의 `quality_metrics`로 간다."""
        values: dict[str, float | int | str] = {"quality_status": self.status}
        if self.fit is not None:
            m = self.fit.measure
            for key, value in (
                ("color_delta_e", m.delta_e),
                ("silhouette_iou", m.iou),
                ("width_change_ratio", m.width_change_abs),
            ):
                if value is not None:
                    values[key] = value
        if self.video.end_frame_gap is not None:
            values["end_frame_gap"] = self.video.end_frame_gap
        if self.video.lightness_range is not None:
            values["garment_lightness_range"] = self.video.lightness_range
        return values

    def to_record(self) -> dict[str, Any]:
        return {
            "check_version": CHECK_VERSION,
            "status": self.status,
            "reasons": self.reasons(),
            "fails": list(self.fails),
            "unverifiable": list(self.unverifiable),
            "fit_frame": None if self.fit is None else self.fit.to_record(),
            "video": self.video.to_record(),
            "criteria": self.criteria.to_record(),
            "human_review_required": self.human_review_required,
            "side_back_fit_check": "not_automated",
            "deformation_check": "not_automated",
            "metrics": self.metrics(),
            "details": self.details,
        }


def judge_fitroom(
    fit: FrameResult | None,
    video: VideoMeasure,
    criteria: FitRoomVideoCriteria,
    *,
    details: Mapping[str, Any] | None = None,
) -> FitRoomCheckResult:
    fails: list[str] = []
    unknown: list[str] = []
    if fit is None:
        unknown.append("no_fit_frame")
    elif fit.status == FAIL:
        fails.append("fit_frame")
    elif fit.status == UNVERIFIABLE:
        unknown.append("fit_frame_unmeasurable")
    if video.black:
        fails.append("black")
    else:
        if video.end_frame_gap is not None and video.end_frame_gap > criteria.end_frame_gap_max:
            fails.append("end_frame_gap")
        if video.garment_missing > 0:
            fails.append("garment_missing")
        lightness_range = video.lightness_range
        if lightness_range is not None and lightness_range > criteria.lightness_range_max:
            fails.append("lightness_range")
    status = FAIL if fails else UNVERIFIABLE if unknown else PASS
    return FitRoomCheckResult(
        status=status,
        fit=fit,
        video=video,
        criteria=criteria,
        fails=tuple(fails),
        unverifiable=tuple(unknown),
        details=dict(details or {}),
    )

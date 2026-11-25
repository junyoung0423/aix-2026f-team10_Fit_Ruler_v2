"""실사 영상 프레임에 대응하는 렌더 클립 프레임 찾기 (D5, #11). 영상 파일을 읽지 않는 순수 계산이다.

실사 영상은 렌더 클립과 길이·fps가 다를 수 있다.
- fal은 출력 프레임 수를 입력 길이 안의 8k + 1로 맞춘다. 그래서 입력보다 짧다
  (D1: 72프레임 입력 → 65프레임 출력).
- 출력 fps(기본 24)가 렌더 fps와 다를 수 있다.

그래서 프레임 번호가 아니라 **시각**으로 맞춘다.

- `time` (기본): 실사 프레임 i의 시각 t = i / fps_실사 → 렌더 프레임 round(t × fps_렌더).
  출력이 입력의 앞부분을 0초부터 같은 속도로 따른다고 본다.
- `stretch`: 위치 비율 i / (n_실사 − 1) → 렌더 프레임 round(비율 × (n_렌더 − 1)).
  출력이 입력 전체를 늘이거나 줄여 담는다고 본다.

`time`이 기본인 근거: D4 실제 호출(렌더 72프레임·24fps, 좌우로 흔들리는 임시 클립 → 실사
65프레임·24fps)에서 프레임마다 옷의 가로 위치를 비교했다. `time`의 평균 오차가 0.25 px(최대 0.68),
`stretch`는 1.67 px(최대 5.6)였다(docs/realize-pipeline.md 3절). fal 모델 페이지에는 이 규칙이
적혀 있지 않다.

`time`에서 실사 시각이 렌더 길이를 넘으면 렌더 마지막 프레임으로 자르고 `clamped=True`로 남긴다.
반올림은 0.5를 올린다(파이썬 `round`의 짝수 반올림을 쓰지 않는다).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Literal

MatchMode = Literal["time", "stretch"]
MATCH_MODES: tuple[str, ...] = ("time", "stretch")

_EPS = 1e-9  # 1/3 × 3처럼 부동소수 오차로 0.5 경계가 아래로 밀리지 않게


@dataclass(frozen=True)
class ClipTiming:
    """영상 하나의 fps와 실제 프레임 수."""

    fps: float
    frame_count: int

    def __post_init__(self) -> None:
        if not (math.isfinite(self.fps) and self.fps > 0):
            raise ValueError(f"fps는 0보다 큰 유한한 값이어야 합니다: {self.fps}")
        if self.frame_count <= 0:
            raise ValueError(f"프레임이 없는 영상입니다: {self.frame_count}")

    @property
    def duration_s(self) -> float:
        return self.frame_count / self.fps

    def time_of(self, index: int) -> float:
        return index / self.fps


@dataclass(frozen=True)
class FrameMatch:
    """실사 프레임 하나와 대응 렌더 프레임."""

    real_index: int
    real_time_s: float
    render_index: int
    render_time_s: float
    mode: str
    clamped: bool  # 계산한 렌더 프레임이 범위를 넘어 마지막 프레임으로 잘랐다

    @property
    def time_gap_s(self) -> float:
        """두 프레임 시각 차이(초). `time`에서 자르지 않았으면 렌더 프레임 반 칸 이하다."""
        return abs(self.render_time_s - self.real_time_s)

    def to_dict(self) -> dict[str, object]:
        return {**asdict(self), "time_gap_s": round(self.time_gap_s, 4)}


def _round_half_up(value: float) -> int:
    return math.floor(value + 0.5 + _EPS)


def match_render_frame(
    real_index: int, real: ClipTiming, render: ClipTiming, mode: str = "time"
) -> FrameMatch:
    """실사 프레임 `real_index`에 대응하는 렌더 프레임.

    Raises:
        ValueError: 프레임 번호가 실사 영상 범위 밖이거나 모르는 방식이다.
    """
    if mode not in MATCH_MODES:
        raise ValueError(f"모르는 매칭 방식입니다: {mode} (가능: {', '.join(MATCH_MODES)})")
    if not 0 <= real_index < real.frame_count:
        raise ValueError(f"실사 프레임 번호가 범위 밖입니다: {real_index} / {real.frame_count}")
    last = render.frame_count - 1
    if mode == "time":
        raw = _round_half_up(real.time_of(real_index) * render.fps)
    else:
        position = real_index / (real.frame_count - 1) if real.frame_count > 1 else 0.0
        raw = _round_half_up(position * last)
    index = min(max(raw, 0), last)
    return FrameMatch(
        real_index=real_index,
        real_time_s=round(real.time_of(real_index), 4),
        render_index=index,
        render_time_s=round(render.time_of(index), 4),
        mode=mode,
        clamped=index != raw,
    )


def coverage(real: ClipTiming, render: ClipTiming) -> dict[str, float | bool]:
    """두 영상 길이 비교. 실사 영상이 렌더보다 길면(`real_longer`) `time` 매칭 끝부분이 잘린다."""
    return {
        "real_duration_s": round(real.duration_s, 4),
        "render_duration_s": round(render.duration_s, 4),
        "duration_ratio": round(real.duration_s / render.duration_s, 4),
        "real_longer": real.duration_s > render.duration_s + _EPS,
    }

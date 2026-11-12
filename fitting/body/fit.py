"""입력 치수에 맞는 몸의 형상 계수·크기를 찾는다 (D3, 외부 SDK·어댑터를 import하지 않는다).

명세서 5장 추천안과 docs/decisions.md "1주차 기준 [제안]" 5절을 따른다.

- 조정하는 값: 형상 계수(betas) 앞 `num_betas`개와 전체 크기(scale).
- 목적값: Σ((다시 잰 값 − 입력) / 허용 오차)² + λ·Σβ². 뒤 항은 형상 사전분포로, 다섯 치수만
  맞추려고 몸이 과하게 변하는 것을 막는다.
- 측정: 인자로 받은 `measure(betas, scale)`. 어댑터가 몸을 만들고 measure_v1로 잰 값을 돌려준다.
  기울기를 수치로 구하므로 반올림하지 않은 값을 받는다(`measure_body(..., round_cm=False)`).
  이 모듈은 몸 모델이 무엇인지 모른다.
- 방법: 위 목적값을 잔차 제곱합으로 보고 `scipy.optimize.least_squares`(trf, 경계 있음, 기울기는
  차분)로 푼다. 단면 둘레는 미분식이 없어서 명세서 5장의 "수치 최적화로 보완"을 따른다.
- 한도: 시작점 `num_starts`개 × 각 `max_evals_per_start`회(기울기 계산 포함 전체 측정 횟수),
  작업 시간 한도(`time_limit_s`). 한 시작점이 통과하면 남은 시작점은 돌리지 않는다(조기 종료).
- 판정: 잰 값을 0.1 cm로 반올림해 비교하고(decisions 5절), 다섯 치수가 모두 허용 오차 이하일
  때만 통과다. 입력을 고치거나 가까운 몸을 성공으로 내지 않는다. 통과한 결과가 없으면 목적값이
  가장 작은 결과를 기록용으로 돌려준다.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.optimize import least_squares

from fitting.body.measure import BodyMeasurementsCm

# `Measurements`의 필드 이름. 결과와 기록의 키도 이 이름을 쓴다.
FIELDS: tuple[str, ...] = ("height_cm", "chest_cm", "waist_cm", "shoulder_cm", "arm_length_cm")

# 몸 허용 오차(cm, 이하 통과). docs/decisions.md "1주차 기준 [제안]" 5절 = 명세서 5장 추천안
BODY_TOLERANCE_CM: Mapping[str, float] = {
    "height_cm": 1.0,
    "chest_cm": 2.0,
    "waist_cm": 2.0,
    "shoulder_cm": 1.0,
    "arm_length_cm": 1.5,
}

# 화면 안내용 한국어 이름 (웹 `MEASUREMENT_LABEL`과 같다)
FIELD_LABEL: Mapping[str, str] = {
    "height_cm": "키",
    "chest_cm": "가슴둘레",
    "waist_cm": "허리둘레",
    "shoulder_cm": "어깨너비",
    "arm_length_cm": "팔길이",
}

# 이 최적화 방법의 버전. 목적값·한도·시작점 고르는 법이 바뀌면 올린다.
FIT_VERSION = "body_fit_v1"

# 측정값을 0.1 cm로 반올림하므로 경계(예: 1.0 cm)의 부동소수 오차를 흡수한다.
_EPS = 1e-9
# 잴 수 없는 몸(측정 함수가 ValueError를 내거나 값이 유한하지 않음)의 목적값
_UNMEASURABLE = 1e12

MeasureFn = Callable[[np.ndarray, float], BodyMeasurementsCm]


@dataclass(frozen=True)
class FitSettings:
    """최적화 설정. 기본값은 명세서 5장 한도(시작점 3개 × 각 200회)다."""

    num_betas: int = 10
    num_starts: int = 3
    max_evals_per_start: int = 200
    beta_bound: float = 3.0  # 형상 계수 범위 ±3 (표준편차 단위)
    # 전체 크기 범위. 입력 키 범위(140~210 cm)를 SMPL 평균 몸(약 172 cm)에서 덮도록 잡았다.
    scale_bounds: tuple[float, float] = (0.75, 1.25)
    prior_weight: float = 0.02  # λ: 형상 사전분포 가중치
    diff_step: float = 0.02  # 차분 기울기의 상대 간격
    scale_step: float = 0.05  # 크기의 변화 단위(x_scale). 형상 계수는 1
    start_spread: float = 1.0  # 2번째 이후 시작점의 형상 계수 표준편차(±2에서 자른다)
    seed: int = 0
    time_limit_s: float | None = None
    tolerance_cm: Mapping[str, float] = field(default_factory=lambda: dict(BODY_TOLERANCE_CM))

    def describe(self) -> dict[str, Any]:
        """기록(JSON)용 설정 값."""
        return {
            "num_betas": self.num_betas,
            "num_starts": self.num_starts,
            "max_evals_per_start": self.max_evals_per_start,
            "beta_bound": self.beta_bound,
            "scale_bounds": list(self.scale_bounds),
            "prior_weight": self.prior_weight,
            "diff_step": self.diff_step,
            "scale_step": self.scale_step,
            "seed": self.seed,
            "time_limit_s": self.time_limit_s,
            "tolerance_cm": dict(self.tolerance_cm),
            "method": "least_squares(trf)",
            "fit_version": FIT_VERSION,
        }


@dataclass(frozen=True)
class Judgement:
    """다시 잰 치수를 입력과 비교한 결과."""

    errors_cm: dict[str, float]  # 다시 잰 값 − 입력, 0.1 cm 단위
    failed_fields: tuple[str, ...]  # 허용 오차를 넘은 치수(FIELDS 순서)

    @property
    def passed(self) -> bool:
        return not self.failed_fields

    @property
    def max_abs_error_cm(self) -> float:
        return max(abs(value) for value in self.errors_cm.values())


def _target_values(target: Any) -> dict[str, float]:
    if isinstance(target, Mapping):
        return {name: float(target[name]) for name in FIELDS}
    return {name: float(getattr(target, name)) for name in FIELDS}


def judge(
    target: Any,
    measured: BodyMeasurementsCm,
    tolerance_cm: Mapping[str, float] = BODY_TOLERANCE_CM,
) -> Judgement:
    """다섯 치수의 오차와 허용 오차를 넘은 치수. 값이 유한하지 않으면 그 치수는 실패다.

    잰 값은 0.1 cm로 반올림한 뒤 비교한다(이미 반올림한 값이면 그대로다).
    """
    goal = _target_values(target)
    values = measured.to_dict()
    errors: dict[str, float] = {}
    failed: list[str] = []
    for name in FIELDS:
        value = float(values[name])
        if not math.isfinite(value):
            errors[name] = math.inf
            failed.append(name)
            continue
        error = round(round(value, 1) - goal[name], 1)
        errors[name] = error
        if abs(error) > tolerance_cm[name] + _EPS:
            failed.append(name)
    return Judgement(errors_cm=errors, failed_fields=tuple(failed))


@dataclass(frozen=True)
class StartRecord:
    """시작점 하나의 결과."""

    index: int
    initial_betas: tuple[float, ...]
    initial_scale: float
    betas: tuple[float, ...]
    scale: float
    objective: float
    measurements: BodyMeasurementsCm | None  # 한 번도 재지 못했으면(시간 한도) None
    judgement: Judgement | None
    evaluations: int
    seconds: float
    stopped_by: str  # "converged"(최적화가 멈춤) · "max_evals"(횟수 한도) · "time_limit"

    @property
    def passed(self) -> bool:
        return self.judgement is not None and self.judgement.passed

    def to_record(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "initial_betas": [round(b, 4) for b in self.initial_betas],
            "initial_scale": round(self.initial_scale, 5),
            "betas": [round(b, 4) for b in self.betas],
            "scale": round(self.scale, 5),
            "objective": None if math.isinf(self.objective) else round(self.objective, 5),
            "measured_cm": None
            if self.measurements is None
            else {name: round(value, 1) for name, value in self.measurements.to_dict().items()},
            "error_cm": None
            if self.judgement is None
            else {
                name: (value if math.isfinite(value) else None)
                for name, value in self.judgement.errors_cm.items()
            },
            "passed": self.passed,
            "evaluations": self.evaluations,
            "seconds": round(self.seconds, 2),
            "stopped_by": self.stopped_by,
        }


@dataclass(frozen=True)
class BodyFitResult:
    """최적화 결과. `passed`가 False면 `betas`·`scale`은 목적값이 가장 작았던 기록용 값이다."""

    target_cm: dict[str, float]
    chosen: StartRecord
    starts: tuple[StartRecord, ...]
    probe: BodyMeasurementsCm  # 형상 계수 0, 크기 1인 평균 몸을 잰 값(크기 시작값을 정할 때 씀)
    evaluations: int  # 평균 몸 1회를 포함한 전체 측정 횟수
    seconds: float
    stopped_by: str  # "converged"(통과해서 멈춤) · "exhausted"(시작점을 다 씀) · "time_limit"
    settings: FitSettings

    @property
    def passed(self) -> bool:
        return self.chosen.passed

    @property
    def betas(self) -> np.ndarray:
        return np.array(self.chosen.betas)

    @property
    def scale(self) -> float:
        return self.chosen.scale

    def to_record(self) -> dict[str, Any]:
        return {
            "target_cm": self.target_cm,
            "passed": self.passed,
            "chosen_start": self.chosen.index,
            "betas": [round(b, 4) for b in self.chosen.betas],
            "scale": round(self.chosen.scale, 5),
            "evaluations": self.evaluations,
            "seconds": round(self.seconds, 2),
            "stopped_by": self.stopped_by,
            "mean_body_cm": self.probe.to_dict(),
            "settings": self.settings.describe(),
            "starts": [start.to_record() for start in self.starts],
        }


class _Stop(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _vector(measured: BodyMeasurementsCm) -> np.ndarray:
    values = measured.to_dict()
    return np.array([float(values[name]) for name in FIELDS])


def fit_body(
    target: Any,
    measure: MeasureFn,
    settings: FitSettings | None = None,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> BodyFitResult:
    """입력 치수(`Measurements` 또는 같은 키의 dict)에 맞는 형상 계수와 크기를 찾는다.

    `measure(betas, scale)`는 그 형상 계수·크기로 만든 몸을 measure_v1로 잰 값이다.
    """
    settings = settings or FitSettings()
    goal_cm = _target_values(target)
    goal = np.array([goal_cm[name] for name in FIELDS])
    tol = np.array([settings.tolerance_cm[name] for name in FIELDS])
    k = settings.num_betas
    started = clock()
    deadline = None if settings.time_limit_s is None else started + settings.time_limit_s

    probe = measure(np.zeros(k), 1.0)
    evaluations = 1
    low, high = settings.scale_bounds
    probe_height = probe.height_cm
    initial_scale = (
        float(np.clip(goal_cm["height_cm"] / probe_height, low, high))
        if math.isfinite(probe_height) and probe_height > 0
        else 1.0
    )
    lower = np.r_[np.full(k, -settings.beta_bound), low]
    upper = np.r_[np.full(k, settings.beta_bound), high]
    x_scale = np.r_[np.ones(k), settings.scale_step]
    prior = math.sqrt(settings.prior_weight)
    unmeasurable = np.full(len(FIELDS) + k, math.sqrt(_UNMEASURABLE / (len(FIELDS) + k)))

    rng = np.random.default_rng(settings.seed)
    starting_points = [np.zeros(k)] + [
        rng.normal(0.0, settings.start_spread, k).clip(-2.0, 2.0)
        for _ in range(settings.num_starts - 1)
    ]

    def run_start(index: int, betas0: np.ndarray) -> StartRecord:
        x0 = np.r_[betas0, initial_scale]
        count = 0
        best: list[Any] = [math.inf, x0.copy(), None]  # 목적값, 값, 잰 결과
        start_time = clock()

        def residuals(params: np.ndarray) -> np.ndarray:
            nonlocal count
            if count >= settings.max_evals_per_start:
                raise _Stop("max_evals")
            if deadline is not None and clock() > deadline:
                raise _Stop("time_limit")
            count += 1
            try:
                measured = measure(params[:k], float(params[k]))
            except ValueError:
                return unmeasurable  # 단면이 비는 등 잴 수 없는 몸은 피한다
            values = _vector(measured)
            if not np.all(np.isfinite(values)):
                return unmeasurable
            result = np.r_[(values - goal) / tol, prior * params[:k]]
            value = float(result @ result)
            if value < best[0]:
                best[0], best[1], best[2] = value, params.copy(), measured
            return result

        stopped_by = "converged"
        try:
            least_squares(
                residuals,
                x0,
                bounds=(lower, upper),
                method="trf",
                diff_step=settings.diff_step,
                x_scale=x_scale,
                xtol=1e-4,
                ftol=1e-4,
                max_nfev=settings.max_evals_per_start,
            )
        except _Stop as stop:
            stopped_by = stop.reason
        else:
            if count >= settings.max_evals_per_start:
                stopped_by = "max_evals"

        params, measured = best[1], best[2]
        return StartRecord(
            index=index,
            initial_betas=tuple(float(b) for b in betas0),
            initial_scale=initial_scale,
            betas=tuple(float(b) for b in params[:k]),
            scale=float(params[k]),
            objective=float(best[0]),
            measurements=measured,
            judgement=None if measured is None else judge(goal_cm, measured, settings.tolerance_cm),
            evaluations=count,
            seconds=clock() - start_time,
            stopped_by=stopped_by,
        )

    records: list[StartRecord] = []
    overall = "exhausted"
    for index, betas0 in enumerate(starting_points):
        if deadline is not None and clock() > deadline:
            overall = "time_limit"
            break
        record = run_start(index, betas0)
        records.append(record)
        evaluations += record.evaluations
        if record.passed:
            overall = "converged"
            break
        if record.stopped_by == "time_limit":
            overall = "time_limit"
            break

    if not records:  # 평균 몸을 재는 동안 시간 한도를 넘었다
        records.append(
            StartRecord(
                index=0,
                initial_betas=tuple(0.0 for _ in range(k)),
                initial_scale=initial_scale,
                betas=tuple(0.0 for _ in range(k)),
                scale=initial_scale,
                objective=math.inf,
                measurements=None,
                judgement=None,
                evaluations=0,
                seconds=0.0,
                stopped_by="time_limit",
            )
        )

    passing = [r for r in records if r.passed]
    chosen = min(passing or records, key=lambda r: r.objective)
    return BodyFitResult(
        target_cm=goal_cm,
        chosen=chosen,
        starts=tuple(records),
        probe=probe,
        evaluations=evaluations,
        seconds=clock() - started,
        stopped_by=overall,
        settings=settings,
    )

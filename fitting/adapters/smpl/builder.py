"""SMPL 몸 생성 어댑터 (D3, `BodyBuilder` 포트, #7).

입력 치수 → `fitting.body.fit_body`로 형상 계수·크기를 찾고 → 측정 자세(A자세 45°)의 몸 메시를
산출물 `{job_id}_body_mesh.obj`로 `artifacts_dir`에 쓴다. 명세서 5장과 docs/decisions.md
"1주차 기준 [제안]" 5절(허용 오차·최적화 한도)을 따른다.

- 성공: `PortResult`. 지표는 B1 계약의 `fitted_*_cm`(만든 몸을 다시 잰 치수 5개)와
  `max_abs_error_cm`, 그리고 `fit_evaluations`(측정 횟수)·`fit_seconds`(걸린 시간)다.
- 실패: 다섯 치수 중 하나라도 허용 오차를 넘거나 메시 값이 유한하지 않으면
  `PortFailure(BODY_FIT_NOT_CONVERGED)`를 던진다. 입력을 고치거나 가까운 몸을 성공으로 내지 않는다.
- 기록: 성공·실패 모두 `work_dir/{job_id}/body_fit.json`에 입력값·생성값·오차·허용 오차·형상
  계수·크기·기준점(가슴·허리 높이, 어깨 끝점)·시작점별 결과·모델 파일 이름을 남긴다.
  `PortError.internal_details_ref`는 이 파일을 `work_dir` 기준 경로(`{job_id}/body_fit.json`)로
  가리킨다. 화면에는 `safe_message`만 보인다.
- 메시: 단위는 미터, y가 위, z가 앞, x가 몸의 왼쪽이다(SMPL 그대로, D2 OBJ와 같다). 치수를 잰
  바로 그 몸이다. 렌더용으로 자세를 바꾸는 일은 착장(C4)에서 하고 따로 기록한다.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import scipy

from fitting.body.fit import (
    BODY_TOLERANCE_CM,
    FIELD_LABEL,
    FIT_VERSION,
    BodyFitResult,
    FitSettings,
    Judgement,
    fit_body,
    judge,
)
from fitting.body.measure import BodyMeasurementsCm, MeasureDebug, measure_body
from fitting.domain import SCHEMA_VERSION, BodyInput, ErrorCode, Stage
from fitting.ports import BODY_OUTPUTS, PortError, PortFailure, PortRequest, PortResult

from .model import SmplModel, a_pose, write_obj

# 측정 자세: T자세에서 팔을 45° 내린 A자세 (measure_v1, D2와 같다)
A_POSE_DEG = 45.0
# 작업 폴더(work_dir/{job_id}/) 안의 기록 파일 이름
BODY_FIT_RECORD = "body_fit.json"
RECORD_VERSION = "body_fit_record_v1"
# 산출물 ID 형식 (docs/adapters.md "산출물 파일 이름 규칙")
_ARTIFACT_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class PosedBodyModel(Protocol):
    """형상 계수·크기로 측정 자세의 몸을 만들고 재는 몸 모델. 테스트는 작은 가짜를 넣는다."""

    @property
    def num_betas(self) -> int: ...

    @property
    def faces(self) -> np.ndarray: ...

    def mesh(self, betas: np.ndarray, scale: float) -> tuple[np.ndarray, np.ndarray]:
        """(정점, 관절), 단위 미터."""
        ...

    def measure(
        self, betas: np.ndarray, scale: float, *, round_cm: bool = True
    ) -> tuple[BodyMeasurementsCm, MeasureDebug]:
        """measure_v1로 잰 치수와 기준점. `round_cm=False`면 0.1 cm로 반올림하지 않는다."""
        ...

    def describe(self) -> dict[str, Any]:
        """기록용 설명(모델 종류, 자세, 정점 수 등)."""
        ...


class SmplPosedBody:
    """SMPL 모델을 A자세(45°)로 만들고 D2의 `measure_body`(measure_v1)로 잰다."""

    def __init__(self, model: SmplModel, pose_deg: float = A_POSE_DEG) -> None:
        self.model = model
        self.pose_deg = pose_deg
        self._pose = a_pose(pose_deg)

    @property
    def num_betas(self) -> int:
        return self.model.num_betas

    @property
    def faces(self) -> np.ndarray:
        return self.model.faces

    def mesh(self, betas: np.ndarray, scale: float) -> tuple[np.ndarray, np.ndarray]:
        out = self.model.forward(betas=np.asarray(betas, dtype=np.float64), body_pose=self._pose)
        return out.vertices * scale, out.joints * scale

    def measure(
        self, betas: np.ndarray, scale: float, *, round_cm: bool = True
    ) -> tuple[BodyMeasurementsCm, MeasureDebug]:
        vertices, joints = self.mesh(betas, scale)
        return measure_body(
            vertices, self.model.faces, joints, self.model.weights, round_cm=round_cm
        )

    def describe(self) -> dict[str, Any]:
        return {
            "kind": "smpl",
            "pose": "a_pose",
            "a_pose_deg": self.pose_deg,
            "num_betas_available": self.num_betas,
            "vertices": int(self.model.v_template.shape[0]),
            "unit": "m",
            "axes": "y up, z front, x body left",
        }


def _finite_or_none(value: float) -> float | None:
    return value if math.isfinite(value) else None


def _point(point: np.ndarray) -> list[float]:
    return [round(float(v), 5) for v in point]


@dataclass
class SmplBodyBuilder:
    """`BodyBuilder` 포트 구현. 만들 때 `fitting.adapters.smpl.factory.make_body_builder`를 쓴다."""

    body_model: PosedBodyModel
    artifacts_dir: Path
    work_dir: Path
    model_name: str  # 모델 파일 이름 (tool_versions·기록용)
    settings: FitSettings = field(default_factory=FitSettings)
    clock: Callable[[], float] = time.monotonic

    def tool_versions(self) -> dict[str, str]:
        return {
            "body_model": f"smpl/{self.model_name}",
            "measurement_protocol": "measure_v1",
            "body_fit": FIT_VERSION,
            "numpy": np.__version__,
            "scipy": scipy.__version__,
        }

    def _settings_for(self, request: PortRequest) -> FitSettings:
        """요청의 시간 한도와 모델의 형상 계수 수에 맞춘 설정."""
        limit = float(request.timeout_seconds)
        if self.settings.time_limit_s is not None:
            limit = min(limit, self.settings.time_limit_s)
        return replace(
            self.settings,
            num_betas=min(self.settings.num_betas, self.body_model.num_betas),
            time_limit_s=limit,
        )

    def build_body(self, request: PortRequest, body: BodyInput) -> PortResult:
        artifact_id = f"{request.job_id}_{BODY_OUTPUTS[0]}"
        if not _ARTIFACT_ID.fullmatch(artifact_id):
            raise ValueError(f"산출물 ID 형식이 아닙니다: {artifact_id!r}")
        settings = self._settings_for(request)

        # 최적화 중에는 기울기를 차분으로 구하므로 반올림하지 않은 값으로 잰다
        fit = fit_body(
            body,
            lambda betas, scale: self.body_model.measure(betas, scale, round_cm=False)[0],
            settings,
            clock=self.clock,
        )

        # 별도 재측정: 고른 값으로 몸을 다시 만들어 0.1 cm 단위로 재고, 메시 값이 유한한지 본다
        # (명세서 5장 "마지막으로 별도 재측정")
        measured, debug = self.body_model.measure(fit.betas, fit.scale)
        vertices, _ = self.body_model.mesh(fit.betas, fit.scale)
        judgement = judge(body, measured, settings.tolerance_cm)
        mesh_finite = bool(np.all(np.isfinite(vertices)))

        if not (judgement.passed and mesh_finite):
            self._write_record(
                request, body, fit, measured, debug, judgement, mesh_finite, settings, None
            )
            raise PortFailure(
                PortError(
                    code=ErrorCode.BODY_FIT_NOT_CONVERGED,
                    stage=Stage.BODY_FIT,
                    retryable=False,
                    safe_message=self._failure_message(judgement, mesh_finite),
                    internal_details_ref=f"{request.job_id}/{BODY_FIT_RECORD}",
                )
            )

        mesh_path = Path(self.artifacts_dir) / f"{artifact_id}.obj"
        write_obj(
            mesh_path,
            vertices,
            self.body_model.faces,
            comment=(
                f"AIMAX body mesh ({self.model_name}, A-pose {A_POSE_DEG:g} deg, meters, y up)\n"
                f"job {request.job_id}, scale {fit.scale:.5f}, "
                f"betas {' '.join(f'{b:.4f}' for b in fit.betas)}"
            ),
        )
        self._write_record(
            request, body, fit, measured, debug, judgement, mesh_finite, settings, artifact_id
        )
        metrics: dict[str, float | int | str] = {
            **measured.fitted_metrics(body),
            "fit_evaluations": fit.evaluations + 1,  # 마지막 재측정 포함
            "fit_seconds": round(fit.seconds, 1),
        }
        return PortResult(
            schema_version=SCHEMA_VERSION,
            output_artifact_ids=[artifact_id],
            metrics=metrics,
            tool_versions=self.tool_versions(),
        )

    @staticmethod
    def _failure_message(judgement: Judgement, mesh_finite: bool) -> str:
        if judgement.failed_fields:
            names = ", ".join(FIELD_LABEL[name] for name in judgement.failed_fields)
            return (
                f"입력한 치수로 3D 몸을 허용 오차 안에 만들지 못했습니다(넘은 치수: {names}). "
                "핏 리포트는 그대로 볼 수 있습니다."
            )
        if not mesh_finite:
            return "3D 몸 메시를 만들지 못했습니다. 핏 리포트는 그대로 볼 수 있습니다."
        return "3D 몸을 만들지 못했습니다. 핏 리포트는 그대로 볼 수 있습니다."

    def _write_record(
        self,
        request: PortRequest,
        body: BodyInput,
        fit: BodyFitResult,
        measured: BodyMeasurementsCm,
        debug: MeasureDebug,
        judgement: Judgement,
        mesh_finite: bool,
        settings: FitSettings,
        artifact_id: str | None,
    ) -> Path:
        passed = artifact_id is not None
        record: dict[str, Any] = {
            "record_version": RECORD_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "job_id": request.job_id,
            "attempt_id": request.attempt_id,
            "trace_id": request.trace_id,
            "passed": passed,
            "artifact_id": artifact_id,
            "failure": None
            if passed
            else {
                "code": ErrorCode.BODY_FIT_NOT_CONVERGED.value,
                "failed_fields": list(judgement.failed_fields),
                "mesh_finite": mesh_finite,
            },
            "measurement_protocol_version": body.measurement_protocol_version,
            "input_cm": fit.target_cm,
            "fitted_cm": measured.to_dict(),
            "error_cm": {k: _finite_or_none(v) for k, v in judgement.errors_cm.items()},
            "tolerance_cm": dict(settings.tolerance_cm or BODY_TOLERANCE_CM),
            "max_abs_error_cm": _finite_or_none(judgement.max_abs_error_cm),
            "betas": list(fit.chosen.betas),  # 다시 만들 수 있게 반올림하지 않는다
            "scale": fit.scale,
            "landmarks": {
                "chest_level_m": round(float(debug.chest_level_m), 5),
                "waist_level_m": round(float(debug.waist_level_m), 5),
                "left_shoulder_point_m": _point(debug.left_shoulder_point),
                "right_shoulder_point_m": _point(debug.right_shoulder_point),
            },
            "body_model": {"model_file": self.model_name, **self.body_model.describe()},
            "optimization": fit.to_record(),
            "tool_versions": self.tool_versions(),
        }
        path = Path(self.work_dir) / request.job_id / BODY_FIT_RECORD
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

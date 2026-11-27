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
- 메시: 단위는 미터, y가 위, z가 앞, x가 몸의 왼쪽이다(SMPL 축, D2 OBJ와 같다). 치수를 잰 바로 그
  몸을 **발바닥(정점 y 최솟값)이 y = 0이 되도록 위아래로만 옮겨** 저장한다. 착장(C4)의 GarmentCode
  패널이 바닥 0인 몸 기준 위치에 놓이기 때문이다. 옮긴 양은 기록의 `translation_m`이고, 기록의
  기준점(`landmarks`)도 옮긴 좌표로 적는다. 평행 이동이라 치수는 그대로다.
- 팔 내리는 길: 몸 모델이 팔 각도를 바꿀 수 있으면(`vertices_at`) 같은 체형으로 팔을 측정 자세
  45°에서 58°(수직에서 32°)까지 6단계로 내린 정점을 계약 밖 산출물
  `{job_id}_body_arms_path.npy`(float32, (6, 정점 수, 3), OBJ와 같은 좌표·이동)로 함께 쓴다.
  착장(C4)이 A자세 착장 뒤 이 길을 따라 팔을 내린다. 치수는 측정 자세 몸으로만 잰다.
- 실패하면 같은 작업의 이전 몸 메시(`{job_id}_body_mesh.obj`)와 팔 내리는 길이 남아 있어도
  지운다. 실패한 작업에 예전 결과가 보이지 않게 하려는 것이다.
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
# 착장(C4)에서 팔을 내릴 때 따라갈 자세: 58°(수직에서 32°)까지 6단계. 더 내리면 겨드랑이에서
# 천이 끼거나 팔이 소매를 뚫었다(25°·20°, docs/body-fit.md 8절)
ARMS_DOWN_DEG = 58.0
ARMS_PATH_STEPS = 6
ARMS_PATH_ROLE = "body_arms_path"  # 계약 밖 산출물 {job_id}_body_arms_path.npy
# 작업 폴더(work_dir/{job_id}/) 안의 기록 파일 이름
BODY_FIT_RECORD = "body_fit.json"
RECORD_VERSION = "body_fit_record_v1"
# 산출물 ID 형식 (docs/adapters.md "산출물 파일 이름 규칙")
_ARTIFACT_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def arms_path_angles(
    start: float = A_POSE_DEG, end: float = ARMS_DOWN_DEG, steps: int = ARMS_PATH_STEPS
) -> tuple[float, ...]:
    """시작 자세 다음부터 끝 자세까지 같은 간격의 팔 각도(°, T자세에서 내린 각)."""
    return tuple(start + (end - start) * (i + 1) / steps for i in range(steps))


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

    def vertices_at(self, betas: np.ndarray, scale: float, arm_deg: float) -> np.ndarray:
        """같은 체형으로 팔만 T자세에서 `arm_deg`만큼 내린 정점(미터)."""
        pose = a_pose(arm_deg)
        out = self.model.forward(betas=np.asarray(betas, dtype=np.float64), body_pose=pose)
        return out.vertices * scale

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
        # 발바닥을 y = 0으로 (착장 C4의 패널 좌표가 바닥 0 기준). 위아래로만 옮겨 치수는 그대로다
        floor = float(np.min(vertices[:, 1])) if mesh_finite else 0.0
        translation = np.array([0.0, -floor, 0.0])
        mesh_path = Path(self.artifacts_dir) / f"{artifact_id}.obj"
        arms_file = self._arms_path_file(request.job_id)

        if not (judgement.passed and mesh_finite):
            stale = mesh_path.is_file()
            if stale:
                mesh_path.unlink()  # 같은 작업의 예전 몸 메시가 실패한 작업에 남지 않게
            arms_file.unlink(missing_ok=True)
            self._write_record(
                request, body, fit, measured, debug, judgement, mesh_finite, settings, None,
                translation=translation, removed_stale_mesh=stale,
            )  # fmt: skip
            raise PortFailure(
                PortError(
                    code=ErrorCode.BODY_FIT_NOT_CONVERGED,
                    stage=Stage.BODY_FIT,
                    retryable=False,
                    safe_message=self._failure_message(judgement, mesh_finite, fit.stopped_by),
                    internal_details_ref=f"{request.job_id}/{BODY_FIT_RECORD}",
                )
            )

        write_obj(
            mesh_path,
            vertices + translation,
            self.body_model.faces,
            comment=(
                f"AIMAX body mesh ({self.model_name}, A-pose {A_POSE_DEG:g} deg, meters, y up, "
                f"feet at y=0, translation_m 0 {translation[1]:.5f} 0)\n"
                f"job {request.job_id}, scale {fit.scale:.5f}, "
                f"betas {' '.join(f'{b:.4f}' for b in fit.betas)}"
            ),
        )
        arms = self._write_arms_path(arms_file, fit, translation)
        self._write_record(
            request, body, fit, measured, debug, judgement, mesh_finite, settings, artifact_id,
            translation=translation, removed_stale_mesh=False, arms_path=arms,
        )  # fmt: skip
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

    def _arms_path_file(self, job_id: str) -> Path:
        return Path(self.artifacts_dir) / f"{job_id}_{ARMS_PATH_ROLE}.npy"

    def _write_arms_path(
        self, path: Path, fit: BodyFitResult, translation: np.ndarray
    ) -> dict[str, Any] | None:
        """팔 내리는 길을 쓰고 기록할 설명을 돌려준다. 몸 모델이 팔 각도를 못 바꾸면 None.

        값이 유한하지 않으면 쓰지 않는다(착장은 A자세 그대로 한다).
        """
        posed = getattr(self.body_model, "vertices_at", None)
        if posed is None:
            path.unlink(missing_ok=True)
            return None
        angles = arms_path_angles()
        frames = np.stack([posed(fit.betas, fit.scale, a) + translation for a in angles])
        record: dict[str, Any] = {
            "angles_deg": [round(a, 4) for a in angles],
            "frame": "OBJ (y up, z front, meters, feet at y=0)",
        }
        if not np.all(np.isfinite(frames)):
            path.unlink(missing_ok=True)
            return {**record, "artifact": None, "skipped": "non_finite"}
        np.save(path, frames.astype(np.float32))
        return {**record, "artifact": path.name, "shape": list(frames.shape), "dtype": "float32"}

    @staticmethod
    def _failure_message(judgement: Judgement, mesh_finite: bool, stopped_by: str = "") -> str:
        if stopped_by == "time_limit" and mesh_finite:
            return (
                "3D 몸을 만드는 데 시간 한도를 넘었습니다. 잠시 뒤 다시 시도해 주세요. "
                "핏 리포트는 그대로 볼 수 있습니다."
            )
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
        *,
        translation: np.ndarray,
        removed_stale_mesh: bool,
        arms_path: dict[str, Any] | None = None,
    ) -> Path:
        passed = artifact_id is not None
        dy = float(translation[1])
        at_bound = sum(abs(b) >= settings.beta_bound - 1e-3 for b in fit.chosen.betas)
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
            # 형상 계수 중 경계(±beta_bound)에 닿은 개수. "비정상 변형"을 정할 때 쓴다(팀 결정 전)
            "betas_at_bound": int(at_bound),
            # OBJ = 모델 출력 + translation_m (발바닥을 y = 0으로). 아래 기준점도 OBJ 좌표다
            "translation_m": [0.0, round(dy, 6), 0.0],
            "landmarks": {
                "frame": "OBJ (y up, z front, meters, feet at y=0)",
                "chest_level_m": round(float(debug.chest_level_m) + dy, 5),
                "waist_level_m": round(float(debug.waist_level_m) + dy, 5),
                "left_shoulder_point_m": _point(debug.left_shoulder_point + translation),
                "right_shoulder_point_m": _point(debug.right_shoulder_point + translation),
            },
            "removed_stale_mesh": removed_stale_mesh,
            # 착장(C4)이 팔을 내릴 때 따라갈 길(계약 밖 산출물). 실패하면 None
            "arms_path": arms_path,
            "body_model": {"model_file": self.model_name, **self.body_model.describe()},
            "optimization": fit.to_record(),
            "tool_versions": self.tool_versions(),
        }
        path = Path(self.work_dir) / request.job_id / BODY_FIT_RECORD
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

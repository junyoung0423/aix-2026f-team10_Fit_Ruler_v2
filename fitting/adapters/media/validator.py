"""실사 영상 검증 어댑터의 프레임 부분 (D5, `Validator` 포트, #11).

D5는 **대표 프레임 추출과 대응 렌더 프레임 매칭**까지 한다. 색·실루엣 판정과 기준(D6)은 아직 없어서
`quality_status`는 늘 `UNVERIFIABLE`이다. 서비스는 PASS가 아니면 검증 단계를 실패
(`VALIDATION_UNVERIFIABLE`)로 끝내므로, 이 어댑터를 고른 설정은 D6 전까지 영상 경로가 검증 단계에서
실패로 끝난다. 그래도 실사 영상과 두 프레임은 저장되고 상태에 산출물 ID가 남는다
(화면에서 볼 수 있다).

- 입력: `input_artifact_ids` = [렌더 클립, 실사 영상] (서비스가 이 순서로 넘긴다).
  파일은 `artifacts_dir/{id}.mp4`.
- 출력 (`VALIDATE_OUTPUTS` 순서, 각 영상의 원래 크기 PNG)
  - `{job_id}_frame.png`: 실사 영상 대표 프레임(앞뒤 20 %를 뺀 구간에서 가장 선명한 프레임)
  - `{job_id}_render_frame.png`: 그 시각에 대응하는 렌더 클립 프레임(`fitting.verify.frame_match`)
- 기록: `work_dir/{job_id}/validate/frames.json` (두 영상 정보, 고른 프레임, 매칭 방식·시각 차이)
- 지표: `quality_status`(UNVERIFIABLE)와 문자열 `frame_method`·`frame_match_mode`만 둔다.
  숫자 지표는 화면의 `quality_metrics`(색 차이·실루엣 겹침 자리)로 가므로 프레임 번호 같은 값은
  기록에만 남긴다.
- 실패 (`PortFailure`, `VALIDATION_UNVERIFIABLE`, `stage=VALIDATE`, `retryable=false`):
  입력 ID가 모자람, 입력 파일 없음, 영상을 읽지 못함, 프레임을 쓰지 못함.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2

from fitting.domain import SCHEMA_VERSION, ErrorCode, GarmentVariant, QualityStatus, Stage
from fitting.ports import VALIDATE_OUTPUTS, PortError, PortFailure, PortRequest, PortResult
from fitting.verify.frame_match import MATCH_MODES

from .frames import FrameMethod, save_frame_pair

VALIDATE_VERSION = "validate_frames_v1"
RECORD_DIR = "validate"
RECORD_NAME = "frames.json"
UNJUDGED_REASON = "D5는 프레임 추출·매칭까지만 한다. 색·실루엣 판정 기준은 D6에서 정한다."


@dataclass
class FrameValidator:
    """`Validator` 포트 구현(프레임 부분).

    만들 때 `fitting.adapters.media.factory.make_validator`를 쓴다.
    """

    artifacts_dir: Path
    work_dir: Path
    method: FrameMethod = "sharpest"
    mode: str = "time"

    def __post_init__(self) -> None:
        if self.mode not in MATCH_MODES:
            raise ValueError(f"모르는 매칭 방식입니다: {self.mode}")
        if self.method not in ("sharpest", "middle"):
            raise ValueError(f"모르는 대표 프레임 방식입니다: {self.method}")

    def tool_versions(self) -> dict[str, str]:
        return {"validate": VALIDATE_VERSION, "opencv": cv2.__version__}

    def _input(self, artifact_id: str) -> Path | None:
        directory = Path(self.artifacts_dir)
        exact = directory / f"{artifact_id}.mp4"
        if exact.is_file():
            return exact
        matches = sorted(p for p in directory.glob(f"{artifact_id}.*") if p.suffix != ".part")
        return matches[0] if matches else None

    def _fail(self, request: PortRequest, message: str, detail: dict[str, Any]) -> PortFailure:
        self._write_record(request, {"state": "failed", "error": detail})
        return PortFailure(
            PortError(
                code=ErrorCode.VALIDATION_UNVERIFIABLE,
                stage=Stage.VALIDATE,
                retryable=False,
                safe_message=message,
                internal_details_ref=f"{request.job_id}/{RECORD_DIR}/{RECORD_NAME}",
            )
        )

    def _write_record(self, request: PortRequest, data: dict[str, Any]) -> None:
        path = Path(self.work_dir) / request.job_id / RECORD_DIR / RECORD_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "job_id": request.job_id,
            "attempt_id": request.attempt_id,
            "input_artifact_ids": list(request.input_artifact_ids),
            "version": VALIDATE_VERSION,
            "method": self.method,
            "mode": self.mode,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **data,
        }
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
        )

    def validate(self, request: PortRequest, garment: GarmentVariant) -> PortResult:
        if len(request.input_artifact_ids) < 2:
            raise self._fail(
                request,
                "검증에 쓸 렌더 클립과 실사 영상이 모두 필요합니다.",
                {"stage": "input", "message": "input_artifact_ids에 [렌더 클립, 실사 영상]이 없음"},
            )
        render_id, video_id = request.input_artifact_ids[0], request.input_artifact_ids[1]
        render_clip, real_video = self._input(render_id), self._input(video_id)
        missing = [i for i, p in ((render_id, render_clip), (video_id, real_video)) if p is None]
        if missing:
            raise self._fail(
                request,
                "검증에 쓸 렌더 클립이나 실사 영상 파일이 없습니다.",
                {"stage": "input", "missing": missing},
            )
        assert render_clip is not None and real_video is not None

        frame_id, render_frame_id = (f"{request.job_id}_{role}" for role in VALIDATE_OUTPUTS)
        directory = Path(self.artifacts_dir)
        try:
            pair = save_frame_pair(
                real_video,
                render_clip,
                directory / f"{frame_id}.png",
                directory / f"{render_frame_id}.png",
                method=self.method,
                mode=self.mode,
            )
        except (ValueError, OSError) as error:
            raise self._fail(
                request,
                "실사 영상이나 렌더 클립에서 프레임을 뽑지 못했습니다.",
                {"stage": "frames", "type": type(error).__name__, "message": str(error)[:2000]},
            ) from error

        self._write_record(
            request,
            {
                "state": "frames_saved",
                "garment_id": garment.id,
                "output_artifact_ids": [frame_id, render_frame_id],
                "quality_status": QualityStatus.UNVERIFIABLE.value,
                "quality_reason": UNJUDGED_REASON,
                "frames": pair.to_dict(),
            },
        )
        return PortResult(
            schema_version=SCHEMA_VERSION,
            output_artifact_ids=[frame_id, render_frame_id],
            metrics={
                "quality_status": QualityStatus.UNVERIFIABLE.value,
                "frame_method": self.method,
                "frame_match_mode": self.mode,
            },
            tool_versions=self.tool_versions(),
        )

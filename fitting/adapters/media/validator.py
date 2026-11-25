"""실사 영상 검증 어댑터 (D5 프레임 + D6 색·실루엣 판정, `Validator` 포트, #11).

명세서 7장 검증 절차를 따른다. 실사 영상 길이의 0~100 % 11프레임을 시각으로 대응하는 렌더 프레임과
비교해 PASS·FAIL·UNVERIFIABLE을 낸다. 판정은 `fitting.verify.visual_check`, 측정은
`fitting.adapters.media.visual_measure`, 기준은 `config/validate/visual_check_v1.json`이다.

- 입력: `input_artifact_ids` = [렌더 클립, 실사 영상] (서비스가 이 순서로 넘긴다).
  파일은 `artifacts_dir/{id}.mp4`. 렌더 옷 마스크는 C5 렌더 어댑터가 쓰는
  `artifacts_dir/{job_id}_render_mask.png`를 찾는다(계약 출력이 아니어서 이름 규칙으로 찾는다).
- 출력 (`VALIDATE_OUTPUTS` 순서, 각 영상의 원래 크기 PNG)
  - `{job_id}_frame.png`: 대표 프레임. PASS면 통과 프레임 중 시간상 가운데에 가장 가까운 프레임,
    아니면 가운데(50 %) 프레임(통과 프레임이 아님을 기록에 남긴다)
  - `{job_id}_render_frame.png`: 그 시각에 대응하는 렌더 클립 프레임
- 기록: `work_dir/{job_id}/validate/validate.json`(방법·기준·프레임별 결과·판정),
  `contact_sheet.png`(11프레임 사람 확인용), `masks/real_XX.png`(프레임별 실사 옷 영역)
- 지표: `quality_status`, 숫자 `color_delta_e`·`silhouette_iou`·`width_change_ratio`(측정된 프레임의
  중앙값), `frames_passed`. 숫자는 화면의 `quality_metrics`로 간다.
- 판정이 FAIL·UNVERIFIABLE이어도 결과(`PortResult`)는 돌려준다. 서비스가 프레임 ID를 상태에 남기고
  검증 단계를 `VALIDATION_FAILED`·`VALIDATION_UNVERIFIABLE`로 끝낸다.
- 렌더 옷 마스크가 없으면 비교하지 않고 UNVERIFIABLE을 돌려준다(가운데 프레임은 저장한다).
- 실패 (`PortFailure`, `VALIDATION_UNVERIFIABLE`, `stage=VALIDATE`, `retryable=false`):
  입력 ID가 모자람, 입력 파일 없음, 영상을 읽지 못함, 프레임을 쓰지 못함.
- 중대한 변형(신체 부위 추가, 소매·넥라인 소실)은 자동으로 찾지 않는다. PASS에도 기록에
  `human_review_required: true`를 남긴다(명세서 7장).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from fitting.domain import SCHEMA_VERSION, ErrorCode, GarmentVariant, QualityStatus, Stage
from fitting.ports import VALIDATE_OUTPUTS, PortError, PortFailure, PortRequest, PortResult
from fitting.verify.frame_match import MATCH_MODES
from fitting.verify.visual_check import CHECK_VERSION

from .fitroom_measure import FitRoomConfig, check_fitroom, fitroom_sheet
from .frames import read_image, save_frame_pair, write_image
from .visual_measure import CheckConfig, VisualRun, as_mask, check_videos, contact_sheet

VALIDATE_VERSION = "validate_visual_v1"
RECORD_DIR = "validate"
RECORD_NAME = "validate.json"
SHEET_NAME = "contact_sheet.png"
MASK_DIR = "masks"
# C5 렌더 어댑터가 쓰는 마스크 이름 끝(`fitting.adapters.blender.renderer.MASK_ROLE`).
# 테스트로 같은지 확인한다.
RENDER_MASK_ROLE = "render_mask"
NO_MASK_REASON = "렌더 옷 마스크 파일이 없어 색·실루엣을 비교하지 못했습니다."
# 피팅룸 실사화가 쓰는 핏 옮기기 프레임 이름
# 끝(`fitting.adapters.fal_fitroom.realizer.FIT_FRAME_ROLE`).
# 테스트로 같은지 확인한다.
FIT_FRAME_ROLE = "realize_fit_frame"
FITROOM_SHEET_NAME = "fitroom_sheet.png"


@dataclass
class VisualValidator:
    """`Validator` 포트 구현. 만들 때 `fitting.adapters.media.factory.make_validator`를 쓴다."""

    artifacts_dir: Path
    work_dir: Path
    config: CheckConfig
    mode: str = "time"
    # 피팅룸 실사화(`fal_fitroom`) 결과 검사 설정. 실사화 중간 사진(핏 옮기기 프레임)이 있으면 쓴다
    fitroom: FitRoomConfig | None = None

    def __post_init__(self) -> None:
        if self.mode not in MATCH_MODES:
            raise ValueError(f"모르는 매칭 방식입니다: {self.mode}")

    def tool_versions(self) -> dict[str, str]:
        return {
            "validate": VALIDATE_VERSION,
            "visual_check": CHECK_VERSION,
            "validate_config": self.config.source,
            **({"fitroom_config": self.fitroom.source} if self.fitroom is not None else {}),
            "opencv": cv2.__version__,
        }

    # -- 파일 ---------------------------------------------------------------

    def _input(self, artifact_id: str) -> Path | None:
        directory = Path(self.artifacts_dir)
        exact = directory / f"{artifact_id}.mp4"
        if exact.is_file():
            return exact
        matches = sorted(p for p in directory.glob(f"{artifact_id}.*") if p.suffix != ".part")
        return matches[0] if matches else None

    def _record_dir(self, request: PortRequest) -> Path:
        return Path(self.work_dir) / request.job_id / RECORD_DIR

    def _write_record(self, request: PortRequest, data: dict[str, Any]) -> None:
        path = self._record_dir(request) / RECORD_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "job_id": request.job_id,
            "attempt_id": request.attempt_id,
            "input_artifact_ids": list(request.input_artifact_ids),
            "version": VALIDATE_VERSION,
            "config": self.config.to_record(),
            "match_mode": self.mode,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **data,
        }
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
        )

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

    def _save_debug(self, request: PortRequest, run: VisualRun) -> dict[str, str]:
        """접촉 시트와 프레임별 실사 옷 영역(운영자 확인용, 작업 폴더)."""
        directory = self._record_dir(request)
        sheet = write_image(directory / SHEET_NAME, contact_sheet(run))
        for fraction, mask in zip(
            self.config.criteria.sample_fractions, run.real_masks, strict=False
        ):
            if mask is not None:
                name = f"real_{round(fraction * 100):03d}.png"
                write_image(directory / MASK_DIR / name, mask.astype(np.uint8) * 255)
        return {"contact_sheet": sheet.name, "masks": MASK_DIR}

    # -- 포트 ---------------------------------------------------------------

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

        directory = Path(self.artifacts_dir)
        frame_id, render_frame_id = (f"{request.job_id}_{role}" for role in VALIDATE_OUTPUTS)
        mask_id = f"{request.job_id}_{RENDER_MASK_ROLE}"
        mask_path = directory / f"{mask_id}.png"
        try:
            if not mask_path.is_file():
                return self._without_mask(
                    request, garment, real_video, render_clip, frame_id, render_frame_id
                )
            render_mask = as_mask(read_image(mask_path, cv2.IMREAD_GRAYSCALE))
            fit_frame = directory / f"{request.job_id}_{FIT_FRAME_ROLE}.png"
            if self.fitroom is not None and fit_frame.is_file():
                return self._validate_fitroom(
                    request, garment, render_clip, real_video, render_mask, fit_frame,
                    frame_id, render_frame_id, mask_id,
                )
            run = check_videos(real_video, render_clip, render_mask, self.config, mode=self.mode)
            real_index, render_index = run.representative_pair()
            write_image(directory / f"{frame_id}.png", run.real_frames[real_index])
            write_image(directory / f"{render_frame_id}.png", run.render_frames[render_index])
            debug = self._save_debug(request, run)
        except (ValueError, OSError) as error:
            raise self._fail(
                request,
                "실사 영상이나 렌더 클립을 검증하지 못했습니다.",
                {"stage": "check", "type": type(error).__name__, "message": str(error)[:2000]},
            ) from error

        result = run.result
        metrics = result.metrics()
        self._write_record(
            request,
            {
                "state": "checked",
                "garment_id": garment.id,
                "render_mask_artifact_id": mask_id,
                "output_artifact_ids": [frame_id, render_frame_id],
                "quality_status": result.status,
                "representative_frames": {"real_index": real_index, "render_index": render_index},
                "debug_files": debug,
                "check": run.to_record(),
                "segmentation_params": self.config.params.to_record(),
            },
        )
        return PortResult(
            schema_version=SCHEMA_VERSION,
            output_artifact_ids=[frame_id, render_frame_id],
            metrics=metrics,
            tool_versions=self.tool_versions(),
        )

    def _validate_fitroom(
        self,
        request: PortRequest,
        garment: GarmentVariant,
        render_clip: Path,
        real_video: Path,
        render_mask: np.ndarray,
        fit_frame: Path,
        frame_id: str,
        render_frame_id: str,
        mask_id: str,
    ) -> PortResult:
        """피팅룸 영상 검사: 렌더 ↔ 핏 옮기기 프레임(정면 핏) + 영상(검은 화면·정면 끝·밝기 흐름).

        대표 프레임은 영상 첫 프레임(정면 실사), 대응 렌더 프레임은 렌더 첫 프레임이다.
        """
        assert self.fitroom is not None
        directory = Path(self.artifacts_dir)
        run = check_fitroom(render_clip, render_mask, fit_frame, real_video, self.config,
                            self.fitroom)
        write_image(directory / f"{frame_id}.png", run.video_frames[0])
        write_image(directory / f"{render_frame_id}.png", run.render_frame)
        sheet = write_image(self._record_dir(request) / FITROOM_SHEET_NAME, fitroom_sheet(run))
        if run.fit_mask is not None:
            write_image(self._record_dir(request) / MASK_DIR / "fit_frame.png",
                        run.fit_mask.astype(np.uint8) * 255)
        result = run.result
        self._write_record(
            request,
            {
                "state": "checked_fitroom",
                "garment_id": garment.id,
                "render_mask_artifact_id": mask_id,
                "fit_frame_artifact_id": fit_frame.stem,
                "output_artifact_ids": [frame_id, render_frame_id],
                "quality_status": result.status,
                "fitroom_config": self.fitroom.to_record(),
                "debug_files": {"fitroom_sheet": sheet.name, "masks": MASK_DIR},
                "check": run.to_record(),
                "segmentation_params": self.config.params.to_record(),
            },
        )
        return PortResult(
            schema_version=SCHEMA_VERSION,
            output_artifact_ids=[frame_id, render_frame_id],
            metrics=result.metrics(),
            tool_versions=self.tool_versions(),
        )

    def _without_mask(
        self,
        request: PortRequest,
        garment: GarmentVariant,
        real_video: Path,
        render_clip: Path,
        frame_id: str,
        render_frame_id: str,
    ) -> PortResult:
        """렌더 옷 마스크가 없을 때: 가운데 프레임 쌍만 저장하고 검증할 수 없다고 돌려준다."""
        directory = Path(self.artifacts_dir)
        pair = save_frame_pair(
            real_video,
            render_clip,
            directory / f"{frame_id}.png",
            directory / f"{render_frame_id}.png",
            method="middle",
            mode=self.mode,
        )
        self._write_record(
            request,
            {
                "state": "no_render_mask",
                "garment_id": garment.id,
                "output_artifact_ids": [frame_id, render_frame_id],
                "quality_status": QualityStatus.UNVERIFIABLE.value,
                "reasons": [NO_MASK_REASON],
                "frames": pair.to_dict(),
            },
        )
        return PortResult(
            schema_version=SCHEMA_VERSION,
            output_artifact_ids=[frame_id, render_frame_id],
            metrics={"quality_status": QualityStatus.UNVERIFIABLE.value},
            tool_versions=self.tool_versions(),
        )

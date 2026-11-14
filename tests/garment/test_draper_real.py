"""실제 Blender 착장 1회 (#8). 마커 blender, CI에서 빠짐. 로컬: pytest -m blender

시연 의류 1벌(`data/prepared/tee_relaxed_l_black_v1`)을 개발용 몸 GarmentCode `mean_all`에 입힌다.
4~8분 걸린다.
착장 검사 통과·실패는 판정하지 않는다(설정 실험 전에는 실패할 수 있다). 성공이면 산출물 두 파일이,
실패면
DRAPE_INVALID와 기록 파일이 있어야 한다. 경로: BLENDER_EXE, FIT_GC_PYTHON, FIT_RULER_GC_ROOT.
"""

import json
import os
from pathlib import Path

import pytest

from fitting.adapters.blender import draper as dr
from fitting.domain import ErrorCode, GarmentVariant
from fitting.ports import PortFailure, PortRequest
from server.wiring import AdapterSettings

pytestmark = pytest.mark.blender

REPO_ROOT = Path(__file__).resolve().parents[2]
GARMENT_ID = "tee_relaxed_l_black_v1"
GC_ROOT = Path(os.environ.get("FIT_RULER_GC_ROOT") or r"C:\dev\GarmentCode")
BODY = GC_ROOT / "assets" / "bodies" / "mean_all.obj"
BLENDER = (
    os.environ.get("BLENDER_EXE") or r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
)


@pytest.mark.skipif(not BODY.is_file(), reason="GarmentCode mean_all.obj 없음")
@pytest.mark.skipif(
    not (REPO_ROOT / "data" / "prepared" / GARMENT_ID / "prep.json").is_file(),
    reason="시연 의류 준비 기록 없음",
)
def test_real_drape_once(tmp_path):
    settings = AdapterSettings.from_environ(
        artifacts_dir=tmp_path / "art",
        environ={"BLENDER_PATH": BLENDER, "FIT_WORK_DIR": str(tmp_path / "work")},
    )
    environ = {k: v for k, v in os.environ.items() if k != "FIT_PREPARED_DIR"}
    environ["FIT_DRAPE_BODY_OBJ"] = str(BODY)
    draper = dr.make_draper(settings, environ)
    garment = GarmentVariant.model_validate(
        json.loads((REPO_ROOT / "data" / "garments" / f"{GARMENT_ID}.json").read_text("utf-8"))
    )
    request = PortRequest(
        job_id="fit_blendertest",
        attempt_id="fit_blendertest_drape_1",
        input_artifact_ids=[],
        config_version="cfg_v1",
        timeout_seconds=600,
        trace_id="fit_blendertest",
    )
    try:
        result = draper.drape(request, garment)
    except PortFailure as failure:
        assert failure.error.code is ErrorCode.DRAPE_INVALID
        assert Path(failure.error.internal_details_ref).is_file()
        return
    for artifact_id, ext in zip(result.output_artifact_ids, ("blend", "png"), strict=True):
        assert (tmp_path / "art" / f"{artifact_id}.{ext}").stat().st_size > 0
    assert result.metrics["drape_ok"] == 1
    assert result.metrics["body_source"] == "dev_env:FIT_DRAPE_BODY_OBJ"

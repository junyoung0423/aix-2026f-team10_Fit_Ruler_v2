"""D5 검증 어댑터(프레임 부분) 테스트. 작은 임시 영상만 쓴다(Blender·fal 없음)."""

import json
import re
from pathlib import Path

import pytest

cv2 = pytest.importorskip("cv2")  # 선택 의존성 realize가 없으면 건너뛴다

from fitting.adapters.media.factory import make_validator  # noqa: E402
from fitting.adapters.media.validator import VALIDATE_VERSION, FrameValidator  # noqa: E402
from fitting.domain import ErrorCode, GarmentVariant, QualityStatus, Stage  # noqa: E402
from fitting.fit import RuleFitCalculator  # noqa: E402
from fitting.ports import (  # noqa: E402
    VALIDATE_OUTPUTS,
    PortFailure,
    PortRequest,
    PortResult,
    Validator,
)
from server.wiring import (  # noqa: E402
    AdapterConfigError,
    AdapterSettings,
    build_ports,
    load_adapter_config,
)
from tools.spike_fal_ltx import write_test_clip  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
GARMENT = GarmentVariant.model_validate_json(
    (ROOT / "data" / "garments" / "tee_regular_m_navy_v1.json").read_text(encoding="utf-8")
)
JOB = "job5"


@pytest.fixture(scope="module")
def clips(tmp_path_factory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("clips")
    return {
        # 렌더 3초 72프레임, 실사 2.71초 65프레임 (D1·D4 실제 호출과 같은 길이 차이)
        "render": write_test_clip(root / "render.mp4", seconds=3.0, size=(96, 128)),
        "real": write_test_clip(root / "real.mp4", seconds=65 / 24, size=(102, 136)),
    }


def setup(tmp_path: Path, clips, *, render: bool = True, real: bool = True) -> FrameValidator:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    if render:
        (artifacts / f"{JOB}_render_clip.mp4").write_bytes(clips["render"].read_bytes())
    if real:
        (artifacts / f"{JOB}_video.mp4").write_bytes(clips["real"].read_bytes())
    return FrameValidator(artifacts_dir=artifacts, work_dir=tmp_path / "work")


def request(inputs: list[str] | None = None) -> PortRequest:
    return PortRequest(
        job_id=JOB,
        attempt_id=f"{JOB}_validate_1",
        input_artifact_ids=[f"{JOB}_render_clip", f"{JOB}_video"] if inputs is None else inputs,
        config_version="test",
        timeout_seconds=120,
        trace_id=JOB,
    )


def record(tmp_path: Path) -> dict:
    path = tmp_path / "work" / JOB / "validate" / "frames.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_saves_the_representative_frame_and_the_matching_render_frame(tmp_path, clips):
    validator = setup(tmp_path, clips)
    result = validator.validate(request(), GARMENT)

    assert isinstance(result, PortResult)
    assert result.output_artifact_ids == [f"{JOB}_{role}" for role in VALIDATE_OUTPUTS]
    frame, render_frame = (tmp_path / "artifacts" / f"{i}.png" for i in result.output_artifact_ids)
    assert cv2.imread(str(frame)).shape == (136, 102, 3)  # 실사 영상 크기 그대로
    assert cv2.imread(str(render_frame)).shape == (128, 96, 3)  # 렌더 클립 크기 그대로
    assert not list((tmp_path / "artifacts").glob("*.part"))

    data = record(tmp_path)
    frames = data["frames"]
    assert frames["match"]["mode"] == "time"
    assert frames["match"]["render_index"] == frames["real_frame"]["index"]  # 같은 24fps
    assert frames["real_video"]["frame_count"] == 65
    assert frames["render_clip"]["frame_count"] == 72
    assert data["state"] == "frames_saved"
    assert data["output_artifact_ids"] == result.output_artifact_ids


def test_quality_is_unverifiable_until_d6_and_no_numbers_reach_the_screen(tmp_path, clips):
    result = setup(tmp_path, clips).validate(request(), GARMENT)

    assert result.metrics["quality_status"] == QualityStatus.UNVERIFIABLE.value
    # 숫자 지표는 화면의 quality_metrics(색 차이·실루엣 겹침 자리)로 가므로 넣지 않는다
    assert not [v for v in result.metrics.values() if isinstance(v, int | float)]
    # 포트 설명의 판정 키를 쓴다
    assert re.search(r"`quality_status`", Validator.__doc__)
    assert result.tool_versions["validate"] == VALIDATE_VERSION
    assert "D6" in record(tmp_path)["quality_reason"]


def test_calling_again_overwrites_the_frames(tmp_path, clips):
    validator = setup(tmp_path, clips)
    first = validator.validate(request(), GARMENT)
    second = validator.validate(request(), GARMENT)
    assert first.output_artifact_ids == second.output_artifact_ids


@pytest.mark.parametrize(
    ("kwargs", "inputs", "missing"),
    [
        ({"real": False}, None, [f"{JOB}_video"]),
        ({"render": False}, None, [f"{JOB}_render_clip"]),
        ({}, [f"{JOB}_render_clip"], None),
    ],
)
def test_missing_inputs_fail_as_unverifiable(tmp_path, clips, kwargs, inputs, missing):
    validator = setup(tmp_path, clips, **kwargs)
    with pytest.raises(PortFailure) as caught:
        validator.validate(request(inputs), GARMENT)

    error = caught.value.error
    assert error.code == ErrorCode.VALIDATION_UNVERIFIABLE
    assert error.stage == Stage.VALIDATE
    assert error.retryable is False
    assert error.internal_details_ref == f"{JOB}/validate/frames.json"
    assert str(tmp_path) not in error.safe_message  # 화면 문장에 경로 없음
    data = record(tmp_path)
    assert data["state"] == "failed"
    if missing is not None:
        assert data["error"]["missing"] == missing


def test_unreadable_video_fails_as_unverifiable(tmp_path, clips):
    validator = setup(tmp_path, clips, real=False)
    (tmp_path / "artifacts" / f"{JOB}_video.mp4").write_bytes(b"not a video")
    with pytest.raises(PortFailure) as caught:
        validator.validate(request(), GARMENT)
    assert caught.value.error.code == ErrorCode.VALIDATION_UNVERIFIABLE
    assert record(tmp_path)["error"]["stage"] == "frames"


def test_unknown_options_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="매칭 방식"):
        FrameValidator(artifacts_dir=tmp_path, work_dir=tmp_path, mode="nearest")
    with pytest.raises(ValueError, match="대표 프레임 방식"):
        FrameValidator(artifacts_dir=tmp_path, work_dir=tmp_path, method="first")


def settings_for(tmp_path: Path) -> AdapterSettings:
    return AdapterSettings(
        artifacts_dir=tmp_path / "artifacts",
        work_dir=tmp_path / "work",
        blender_path=None,
        smpl_model_dir=None,
        fal_key_present=False,
        repo_root=ROOT,
    )


def test_factory_builds_the_validator(tmp_path):
    validator = make_validator(settings_for(tmp_path))
    assert isinstance(validator, FrameValidator)
    assert validator.artifacts_dir == tmp_path / "artifacts"
    assert validator.work_dir == tmp_path / "work"


def test_visual_real_config_wires_all_five_real_adapters():
    config = load_adapter_config("visual_real", environ={})
    assert config.ports == {
        "body": "fitting.adapters.smpl.factory:make_body_builder",
        "drape": "fitting.adapters.blender.draper:make_draper",
        "render": "fitting.adapters.blender.renderer:make_renderer",
        "realize": "fitting.adapters.fal_ltx.factory:make_realizer",
        "validate": "fitting.adapters.media.factory:make_validator",
    }
    assert load_adapter_config("default", environ={}).ports["validate"] == "fake"


def test_visual_real_config_stops_on_missing_tools_but_the_validator_builds(tmp_path):
    config = load_adapter_config("visual_real", environ={})
    with pytest.raises(AdapterConfigError) as caught:
        build_ports(config, settings_for(tmp_path), fit=RuleFitCalculator())
    message = str(caught.value)
    for port in ("body", "drape", "render", "realize"):
        assert f"{port}: " in message
    assert "validate: " not in message  # OpenCV만 있으면 된다

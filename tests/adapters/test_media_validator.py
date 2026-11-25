"""검증 어댑터(D5 프레임 + D6 색·실루엣 판정) 테스트. 작은 합성 영상만 쓴다(Blender·fal 없음)."""

import json
import re
from pathlib import Path

import pytest

cv2 = pytest.importorskip("cv2")  # 선택 의존성 realize가 없으면 건너뛴다

from fitting.adapters.blender.renderer import MASK_ROLE  # noqa: E402
from fitting.adapters.media.factory import make_validator  # noqa: E402
from fitting.adapters.media.validator import (  # noqa: E402
    NO_MASK_REASON,
    RENDER_MASK_ROLE,
    VALIDATE_VERSION,
    VisualValidator,
)
from fitting.adapters.media.visual_measure import load_check_config  # noqa: E402
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
from tests.adapters import visual_clips as vc  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
GARMENT = GarmentVariant.model_validate_json(
    (ROOT / "data" / "garments" / "tee_regular_m_navy_v1.json").read_text(encoding="utf-8")
)
JOB = "job6"
CONFIG = load_check_config()


@pytest.fixture(scope="module")
def clips(tmp_path_factory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("clips")
    render, mask = vc.render_clip(root / "render.mp4")
    return {
        "render": render,
        "mask": vc.write_mask(root / "mask.png", mask),
        "same": vc.real_clip(root / "same.mp4"),
        "recolor": vc.real_clip(root / "recolor.mp4", garment=vc.RED),
        "wider": vc.real_clip(root / "wider.mp4", widen=1.12),
    }


def setup(
    tmp_path: Path,
    clips,
    *,
    real: str | None = "same",
    render: bool = True,
    mask: bool = True,
) -> VisualValidator:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    if render:
        (artifacts / f"{JOB}_render_clip.mp4").write_bytes(clips["render"].read_bytes())
    if mask:
        (artifacts / f"{JOB}_render_mask.png").write_bytes(clips["mask"].read_bytes())
    if real is not None:
        (artifacts / f"{JOB}_video.mp4").write_bytes(clips[real].read_bytes())
    return VisualValidator(artifacts_dir=artifacts, work_dir=tmp_path / "work", config=CONFIG)


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
    path = tmp_path / "work" / JOB / "validate" / "validate.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_passing_video_returns_pass_with_metrics_and_frames(tmp_path, clips):
    result = setup(tmp_path, clips).validate(request(), GARMENT)

    assert isinstance(result, PortResult)
    assert result.output_artifact_ids == [f"{JOB}_{role}" for role in VALIDATE_OUTPUTS]
    metrics = result.metrics
    assert metrics["quality_status"] == QualityStatus.PASS.value
    assert metrics["silhouette_iou"] >= 0.85
    assert metrics["color_delta_e"] <= 8.0
    assert metrics["frames_passed"] == 11
    assert set(metrics) == {
        "quality_status",
        "color_delta_e",
        "silhouette_iou",
        "width_change_ratio",
        "frames_passed",
    }
    # 포트 설명의 지표 키를 쓴다
    for key in ("quality_status", "color_delta_e", "silhouette_iou"):
        assert re.search(rf"`{key}`", Validator.__doc__)

    frame, render_frame = (tmp_path / "artifacts" / f"{i}.png" for i in result.output_artifact_ids)
    assert cv2.imread(str(frame)).shape == (vc.REAL_SIZE[1], vc.REAL_SIZE[0], 3)  # 원래 크기
    assert cv2.imread(str(render_frame)).shape == (vc.RENDER_SIZE[1], vc.RENDER_SIZE[0], 3)
    assert not list((tmp_path / "artifacts").glob("*.part"))


def test_record_keeps_method_criteria_frames_and_review_flag(tmp_path, clips):
    validator = setup(tmp_path, clips)
    validator.validate(request(), GARMENT)
    data = record(tmp_path)

    assert data["state"] == "checked"
    assert data["version"] == VALIDATE_VERSION
    assert data["config"]["source"] == "visual_check_v1@1.0.0"
    assert len(data["config"]["sha256"]) == 64
    assert data["render_mask_artifact_id"] == f"{JOB}_render_mask"
    assert data["representative_frames"] == {"real_index": 12, "render_index": 12}
    check = data["check"]
    assert check["status"] == "PASS"
    assert check["criteria"]["color_delta_e_max"] == 8.0
    assert len(check["frames"]) == 11
    assert check["human_review_required"] is True
    assert check["representative"]["passed"] is True
    assert data["segmentation_params"]["method"] == "color_seed_v1"
    work = tmp_path / "work" / JOB / "validate"
    assert (work / "contact_sheet.png").is_file()
    assert len(list((work / "masks").glob("real_*.png"))) == 11


@pytest.mark.parametrize(
    ("real", "fails"),
    [("recolor", {"color_delta_e"}), ("wider", {"chest_width", "hem_width"})],
)
def test_failing_videos_return_fail_with_frames_and_reasons(tmp_path, clips, real, fails):
    result = setup(tmp_path, clips, real=real).validate(request(), GARMENT)
    assert result.metrics["quality_status"] == QualityStatus.FAIL.value
    assert result.metrics["frames_passed"] == 0
    assert all((tmp_path / "artifacts" / f"{i}.png").is_file() for i in result.output_artifact_ids)
    check = record(tmp_path)["check"]
    assert {code for frame in check["frames"] for code in frame["fails"]} == fails
    assert check["representative"]["passed"] is False  # 가운데 프레임을 사람 확인용으로 남김
    assert check["human_review_required"] is False


def test_without_render_mask_frames_are_saved_and_quality_is_unverifiable(tmp_path, clips):
    result = setup(tmp_path, clips, mask=False).validate(request(), GARMENT)
    assert result.metrics == {"quality_status": QualityStatus.UNVERIFIABLE.value}
    assert all((tmp_path / "artifacts" / f"{i}.png").is_file() for i in result.output_artifact_ids)
    data = record(tmp_path)
    assert data["state"] == "no_render_mask"
    assert data["reasons"] == [NO_MASK_REASON]


def test_mask_name_matches_the_c5_renderer():
    assert RENDER_MASK_ROLE == MASK_ROLE


def test_calling_again_overwrites_the_frames(tmp_path, clips):
    validator = setup(tmp_path, clips)
    first = validator.validate(request(), GARMENT)
    second = validator.validate(request(), GARMENT)
    assert first.output_artifact_ids == second.output_artifact_ids
    assert first.metrics == second.metrics


@pytest.mark.parametrize(
    ("kwargs", "inputs", "missing"),
    [
        ({"real": None}, None, [f"{JOB}_video"]),
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
    assert error.internal_details_ref == f"{JOB}/validate/validate.json"
    assert str(tmp_path) not in error.safe_message  # 화면 문장에 경로 없음
    data = record(tmp_path)
    assert data["state"] == "failed"
    if missing is not None:
        assert data["error"]["missing"] == missing


def test_unreadable_video_fails_as_unverifiable(tmp_path, clips):
    validator = setup(tmp_path, clips, real=None)
    (tmp_path / "artifacts" / f"{JOB}_video.mp4").write_bytes(b"not a video")
    with pytest.raises(PortFailure) as caught:
        validator.validate(request(), GARMENT)
    assert caught.value.error.code == ErrorCode.VALIDATION_UNVERIFIABLE
    assert record(tmp_path)["error"]["stage"] == "check"


def test_unknown_match_mode_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="매칭 방식"):
        VisualValidator(artifacts_dir=tmp_path, work_dir=tmp_path, config=CONFIG, mode="nearest")


def settings_for(tmp_path: Path) -> AdapterSettings:
    return AdapterSettings(
        artifacts_dir=tmp_path / "artifacts",
        work_dir=tmp_path / "work",
        blender_path=None,
        smpl_model_dir=None,
        fal_key_present=False,
        repo_root=ROOT,
    )


def test_factory_builds_the_validator_with_the_default_criteria(tmp_path):
    validator = make_validator(settings_for(tmp_path))
    assert isinstance(validator, VisualValidator)
    assert validator.artifacts_dir == tmp_path / "artifacts"
    assert validator.work_dir == tmp_path / "work"
    assert validator.config.source == "visual_check_v1@1.0.0"
    assert validator.tool_versions()["validate_config"] == "visual_check_v1@1.0.0"


def test_factory_reports_a_broken_criteria_file(tmp_path, monkeypatch):
    from fitting.adapters.media import visual_measure

    broken = tmp_path / "broken.json"
    broken.write_text('{"name": "x"}', encoding="utf-8")
    monkeypatch.setattr(visual_measure, "DEFAULT_CHECK_CONFIG", broken)
    with pytest.raises(AdapterConfigError, match="검증 설정"):
        make_validator(settings_for(tmp_path))


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

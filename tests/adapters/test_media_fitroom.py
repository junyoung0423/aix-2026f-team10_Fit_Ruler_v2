"""피팅룸 결과 검증(측정 + 검증 어댑터 분기)과 D6 화면비 맞추기 테스트. 합성 그림·영상만 쓴다."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

cv2 = pytest.importorskip("cv2")  # 선택 의존성 realize가 없으면 건너뛴다
np = pytest.importorskip("numpy")

from fitting.adapters.media.fitroom_measure import (  # noqa: E402
    DEFAULT_FITROOM_CONFIG,
    FitAlign,
    check_fitroom,
    load_fitroom_config,
    measure_video,
)
from fitting.adapters.media.validator import VisualValidator  # noqa: E402
from fitting.adapters.media.visual_measure import check_videos, cover, load_check_config  # noqa: E402
from fitting.domain import GarmentVariant, QualityStatus  # noqa: E402
from fitting.ports import PortRequest  # noqa: E402
from fitting.verify.visual_check import FAIL, PASS  # noqa: E402
from tests.adapters import visual_clips as vc  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
GARMENT = GarmentVariant.model_validate_json(
    (ROOT / "data" / "garments" / "tee_regular_m_navy_v1.json").read_text(encoding="utf-8")
)
CONFIG = load_check_config()
FITROOM = load_fitroom_config()
JOB = "jobfv"
TURN_SIZE = (180, 240)
TURN_FRAMES = 73  # 24fps 3초 + 1: 1초 간격 시점 0·1·2·3초
# 옷이 밝아진 프레임(BGR). 네이비 L* 약 21 → 28로, 옷 색 거리 25 안이라 옷으로 잡히고 밝기만 바뀐다
LIGHT_NAVY = (100, 66, 48)


def png(path: Path, image) -> Path:
    ok, buf = cv2.imencode(".png", image)
    assert ok
    path.write_bytes(buf.tobytes())
    return path


def turn(garment_at=lambda i: vc.NAVY, last=None) -> list:
    frames = [vc.draw(TURN_SIZE, real=True, garment=garment_at(i), seed=i)[0]
              for i in range(TURN_FRAMES)]
    if last is not None:
        frames[-1] = last
    return frames


def fal_like(frame, size=(176, 224)):
    """fal처럼 세로에 맞춰 같은 비율로 줄이고 좌우를 덧붙인 프레임(704x896 출력과 같은 4.8 %)."""
    w, h = size
    scaled = cv2.resize(frame, (round(frame.shape[1] * h / frame.shape[0]), h))
    pad = w - scaled.shape[1]
    return cv2.copyMakeBorder(scaled, 0, 0, pad // 2, pad - pad // 2, cv2.BORDER_REPLICATE)


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("fitroom_check")
    render, mask = vc.render_clip(root / "render.mp4")
    middle = range(20, 53)  # 1·2초 시점
    widened = vc.draw(TURN_SIZE, real=True, widen=1.4, seed=99)[0]
    return {
        "render": render,
        "mask": vc.write_mask(root / "mask.png", mask),
        "mask_array": mask,
        "fit": png(root / "fit.png", vc.draw(vc.RENDER_SIZE, real=True, seed=5)[0]),
        "fit_red": png(root / "fit_red.png",
                       vc.draw(vc.RENDER_SIZE, real=True, garment=vc.RED, seed=5)[0]),
        "steady": vc.write_clip(root / "steady.mp4", turn()),
        "flicker": vc.write_clip(root / "flicker.mp4",
                                 turn(lambda i: LIGHT_NAVY if i in middle else vc.NAVY)),
        "recolor": vc.write_clip(root / "recolor.mp4",
                                 turn(lambda i: vc.RED if i in middle else vc.NAVY)),
        "unfinished": vc.write_clip(root / "unfinished.mp4", turn(last=widened)),
        "black": vc.write_clip(root / "black.mp4",
                               [np.zeros((TURN_SIZE[1], TURN_SIZE[0], 3), np.uint8)] * TURN_FRAMES),
        "same_length": vc.real_clip(root / "same_length.mp4"),  # 렌더와 같은 길이(옛 실사화)
        "fal_like": vc.write_clip(root / "fal_like.mp4",
                                  [fal_like(vc.draw(vc.RENDER_SIZE, real=True, seed=i)[0])
                                   for i in range(vc.FRAMES)]),
    }


# --- 측정 --------------------------------------------------------------------------------


def test_config_loads():
    assert FITROOM.source == "fitroom_check_v1@1.1.0"
    assert FITROOM.path == "config/validate/fitroom_check_v1.json"
    assert DEFAULT_FITROOM_CONFIG.is_file()
    assert FITROOM.align == FitAlign(0.9, 1.15, 0.01, 0.08)  # 사진 맞춤 범위
    assert FITROOM.to_record()["fit_frame_align"]["scale_max"] == 1.15


def test_fit_align_range_is_checked():
    assert FitAlign.from_config({}) == FitAlign()  # 블록이 없으면 맞추지 않는다
    block = {"align_scale_min": 0.9, "align_scale_max": 1.15, "align_scale_step": 0.01,
             "align_shift_max_frac": 0.08}
    for key, value in (("align_scale_min", 1.1), ("align_scale_max", 0.95),
                       ("align_scale_step", 0), ("align_shift_max_frac", 0.5)):
        with pytest.raises(ValueError, match="fit_frame"):
            FitAlign.from_config({"fit_frame": {**block, key: value}})
    with pytest.raises(ValueError, match="align_scale_step"):
        FitAlign.from_config({"fit_frame": {k: v for k, v in block.items()
                                             if k != "align_scale_step"}})


def test_steady_video_has_small_end_gap_and_lightness_range(media):
    measure, frames, times = measure_video(media["steady"], FITROOM.video)
    assert times == [0.0, 1.0, 2.0, 3.0]
    assert len(frames) == 4
    assert measure.black is False
    assert measure.garment_missing == 0
    assert len(measure.lightness) == 4
    assert measure.end_frame_gap < 4.0
    assert measure.lightness_range < 6.0
    assert measure.jump_ratio <= FITROOM.video.jump_ratio_max


def test_lightened_garment_raises_the_lightness_range(media):
    measure, _, _ = measure_video(media["flicker"], FITROOM.video)
    assert measure.garment_missing == 0  # 옷으로는 잡힌다
    assert measure.lightness_range > 6.0


def test_recolored_garment_is_counted_as_missing_not_skipped(media):
    measure, _, _ = measure_video(media["recolor"], FITROOM.video)
    assert measure.garment_missing == 2  # 1·2초 시점
    assert len(measure.lightness) == 2


def test_jump_is_found_at_the_frame_that_changed_at_once(media):
    measure, _, _ = measure_video(media["recolor"], FITROOM.video)
    assert measure.jump_ratio > FITROOM.video.jump_ratio_max
    assert measure.jump_at_s == 0.83  # 20번째 프레임(24fps)에서 빨강으로 바뀐다


def test_smooth_motion_passes_and_a_cut_is_a_jump(tmp_path):
    """옆으로 1픽셀씩 움직이는 영상은 통과, 중간에 60픽셀 끊겨 넘어가면 화면 튐."""
    base = vc.draw(TURN_SIZE, real=True, seed=1)[0]
    smooth = [np.roll(base, i, axis=1) for i in range(TURN_FRAMES)]
    cut = [np.roll(base, i + (60 if i >= 36 else 0), axis=1) for i in range(TURN_FRAMES)]
    calm, _, _ = measure_video(vc.write_clip(tmp_path / "smooth.mp4", smooth), FITROOM.video)
    jumped, _, _ = measure_video(vc.write_clip(tmp_path / "cut.mp4", cut), FITROOM.video)
    assert calm.jump_ratio <= FITROOM.video.jump_ratio_max
    assert jumped.jump_ratio > FITROOM.video.jump_ratio_max
    assert jumped.jump_at_s == 1.5  # 36번째 프레임


def test_black_video_is_detected(media):
    measure, _, _ = measure_video(media["black"], FITROOM.video)
    assert measure.black is True
    assert measure.end_frame_gap is None
    assert measure.garment_missing == 0  # 검은 영상은 검은 화면 하나로만 실패시킨다
    assert measure.jump_ratio is None


@pytest.mark.parametrize(
    ("fit", "video", "status", "fails"),
    [
        ("fit", "steady", PASS, ()),
        ("fit_red", "steady", FAIL, ("fit_frame",)),
        ("fit", "flicker", FAIL, ("lightness_range",)),
        # 아래 둘은 한 프레임 만에 바뀌는 합성 영상이라 화면 튐으로도 잡힌다
        ("fit", "recolor", FAIL, ("garment_missing", "frame_jump")),
        ("fit", "unfinished", FAIL, ("end_frame_gap", "frame_jump")),
        ("fit", "black", FAIL, ("black",)),
    ],
)
def test_reference_cases(media, fit, video, status, fails):
    run = check_fitroom(media["render"], media["mask_array"], media[fit], media[video], CONFIG,
                        FITROOM)
    assert run.result.status == status
    assert run.result.fails == fails
    assert run.fit_frame.shape[:2] == media["mask_array"].shape


def test_a_zoomed_out_fit_photo_is_aligned_before_measuring(tmp_path, media):
    """사진 모델이 10 % 멀리 찍은 사진(q13)은 크기를 맞춘 뒤 재서 통과한다. 맞추지 않으면 실패."""
    photo = vc.draw(vc.RENDER_SIZE, real=True, seed=5)[0]
    h, w = photo.shape[:2]
    far = cv2.warpAffine(photo, np.float32([[0.9, 0, 0.05 * w], [0, 0.9, 0.05 * h]]), (w, h),
                         borderMode=cv2.BORDER_REPLICATE)
    path = png(tmp_path / "far.png", far)
    run = check_fitroom(media["render"], media["mask_array"], path, media["steady"], CONFIG,
                        FITROOM)
    assert run.result.status == PASS
    align = run.segment_info["alignment"]
    assert align["scale"] == pytest.approx(1 / 0.9, abs=0.03)  # 1/4 크기에서 찾아 몇 단계 어긋난다
    assert align["iou_aligned_search"] > align["iou_before"]
    fixed = replace(FITROOM, align=FitAlign())
    run = check_fitroom(media["render"], media["mask_array"], path, media["steady"], CONFIG,
                        fixed)
    assert run.result.fails == ("fit_frame",)
    assert run.segment_info["alignment"]["scale"] == 1.0  # 맞추지 않은 것도 기록에 남는다


# --- 검증 어댑터 분기 ----------------------------------------------------------------------


def setup(tmp_path: Path, media, *, video="steady", fit="fit", mask=True, fitroom=True):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / f"{JOB}_render_clip.mp4").write_bytes(media["render"].read_bytes())
    (artifacts / f"{JOB}_video.mp4").write_bytes(media[video].read_bytes())
    if mask:
        (artifacts / f"{JOB}_render_mask.png").write_bytes(media["mask"].read_bytes())
    if fit:
        (artifacts / f"{JOB}_realize_fit_frame.png").write_bytes(media[fit].read_bytes())
    return VisualValidator(artifacts_dir=artifacts, work_dir=tmp_path / "work", config=CONFIG,
                           fitroom=FITROOM if fitroom else None)


def request() -> PortRequest:
    return PortRequest(job_id=JOB, attempt_id=f"{JOB}_validate_1",
                       input_artifact_ids=[f"{JOB}_render_clip", f"{JOB}_video"],
                       config_version="test", timeout_seconds=120, trace_id=JOB)


def record(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "work" / JOB / "validate" / "validate.json").read_text("utf-8"))


def test_validator_uses_the_fitroom_check_when_the_fit_frame_exists(tmp_path, media):
    result = setup(tmp_path, media).validate(request(), GARMENT)
    assert result.metrics["quality_status"] == QualityStatus.PASS.value
    assert {"color_delta_e", "silhouette_iou", "width_change_ratio", "end_frame_gap",
            "garment_lightness_range", "frame_jump_ratio"} <= set(result.metrics)
    assert "frames_passed" not in result.metrics  # 11프레임 규칙을 쓰지 않는다
    frame, render_frame = (tmp_path / "artifacts" / f"{i}.png" for i in result.output_artifact_ids)
    assert cv2.imread(str(frame)).shape == (TURN_SIZE[1], TURN_SIZE[0], 3)  # 영상 첫 프레임
    assert cv2.imread(str(render_frame)).shape == (vc.RENDER_SIZE[1], vc.RENDER_SIZE[0], 3)
    data = record(tmp_path)
    assert data["state"] == "checked_fitroom"
    assert data["fit_frame_artifact_id"] == f"{JOB}_realize_fit_frame"
    assert data["check"]["check_version"] == "fitroom_check_v1"
    assert data["check"]["human_review_required"] is True
    assert data["check"]["video_times"] == [0.0, 1.0, 2.0, 3.0]
    assert data["fitroom_config"]["source"] == "fitroom_check_v1@1.1.0"
    directory = tmp_path / "work" / JOB / "validate"
    assert (directory / "fitroom_sheet.png").is_file()
    assert (directory / "masks" / "fit_frame.png").is_file()
    assert result.tool_versions["fitroom_config"] == "fitroom_check_v1@1.1.0"


@pytest.mark.parametrize(
    ("video", "reason"),
    [
        ("flicker", "시간에 따라 옷 밝기가 크게 흔들림"),
        ("recolor", "옷 색을 찾지 못한 시점이 있음(옷 색이 크게 바뀌었거나 화면 밖)"),
    ],
)
def test_validator_reports_a_failing_fitroom_video(tmp_path, media, video, reason):
    result = setup(tmp_path, media, video=video).validate(request(), GARMENT)
    assert result.metrics["quality_status"] == QualityStatus.FAIL.value
    assert reason in record(tmp_path)["check"]["reasons"]


def test_without_the_fit_frame_the_11_frame_check_is_used(tmp_path, media):
    result = setup(tmp_path, media, video="same_length", fit=None).validate(request(), GARMENT)
    assert record(tmp_path)["state"] == "checked"
    assert result.metrics["quality_status"] == QualityStatus.PASS.value
    assert "frames_passed" in result.metrics


def test_a_turn_video_without_the_fit_frame_is_unverifiable_by_the_11_frame_check(tmp_path, media):
    """회전 영상은 렌더 클립과 길이가 달라 11프레임 규칙으로 잴 수 없다(늘려 맞추지 않는다)."""
    result = setup(tmp_path, media, fit=None).validate(request(), GARMENT)
    data = record(tmp_path)
    assert data["state"] == "checked"
    assert result.metrics == {"quality_status": QualityStatus.UNVERIFIABLE.value}
    assert any("길이 차이" in b for b in data["check"]["blockers"])


def test_without_fitroom_config_the_11_frame_check_is_used(tmp_path, media):
    setup(tmp_path, media, video="same_length", fitroom=False).validate(request(), GARMENT)
    assert record(tmp_path)["state"] == "checked"


def test_without_render_mask_the_result_is_unverifiable(tmp_path, media):
    result = setup(tmp_path, media, mask=False).validate(request(), GARMENT)
    assert result.metrics == {"quality_status": QualityStatus.UNVERIFIABLE.value}
    assert record(tmp_path)["state"] == "no_render_mask"


def test_unreadable_fit_frame_fails_the_port(tmp_path, media):
    from fitting.ports import PortFailure

    validator = setup(tmp_path, media)
    (tmp_path / "artifacts" / f"{JOB}_realize_fit_frame.png").write_bytes(b"not a png")
    with pytest.raises(PortFailure) as caught:
        validator.validate(request(), GARMENT)
    assert caught.value.error.code.value == "VALIDATION_UNVERIFIABLE"
    assert "핏 확인 사진" in record(tmp_path)["error"]["message"]


# --- D6 화면비 맞추기 ---------------------------------------------------------------------


def test_cover_scales_evenly_and_crops_the_center():
    frame = np.zeros((224, 176, 3), np.uint8)
    frame[:, :2] = 255  # 덧붙은 가장자리(같은 비율로 키우면 약 2.3픽셀, 양쪽 4픽셀씩 잘린다)
    frame[:, -2:] = 255
    out = cover(frame, (192, 256))
    assert out.shape == (256, 192, 3)
    assert out[:, :2].max() == 0 and out[:, -2:].max() == 0
    assert cover(frame, (176, 224)) is frame


def test_fal_aspect_change_is_matched_instead_of_blocked(media):
    run = check_videos(media["fal_like"], media["render"], media["mask_array"], CONFIG)
    assert run.result.blockers == ()
    assert run.result.status == PASS
    assert run.result.details["aspect_gap"] == pytest.approx(0.0476, abs=0.001)

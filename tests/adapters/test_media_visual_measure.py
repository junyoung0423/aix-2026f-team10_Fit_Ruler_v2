"""D6 색·실루엣 측정(OpenCV) 테스트: 합성 통과·실패 기준 사례와 검증 불가 조건. Blender·fal 없음."""

import json

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")  # 선택 의존성 realize가 없으면 건너뛴다

from fitting.adapters.media.visual_measure import (  # noqa: E402
    DEFAULT_CHECK_CONFIG,
    SegmentParams,
    check_videos,
    contact_sheet,
    dilate,
    erode,
    fill_holes,
    load_check_config,
    segment_garment,
)
from fitting.verify.visual_check import FAIL, PASS, UNVERIFIABLE, mask_iou  # noqa: E402
from tests.adapters import visual_clips as vc  # noqa: E402

CONFIG = load_check_config()


@pytest.fixture(scope="module")
def clips(tmp_path_factory) -> dict[str, object]:
    root = tmp_path_factory.mktemp("visual")
    render, mask = vc.render_clip(root / "render.mp4")
    return {
        "root": root,
        "render": render,
        "mask": mask,
        "same": vc.real_clip(root / "same.mp4"),
        "recolor": vc.real_clip(root / "recolor.mp4", garment=vc.RED),
        "wider": vc.real_clip(root / "wider.mp4", widen=1.12),
        # 25프레임에서 11프레임은 0, 2, 5, 7, 10, 12, 14, 17, 19, 22, 24번
        "bad2": vc.real_clip(root / "bad2.mp4", bad_frames=[0, 1, 23, 24]),
        "bad4": vc.real_clip(root / "bad4.mp4", bad_frames=[0, 1, 2, 3, 21, 22, 23, 24]),
    }


def test_config_loads_with_source_and_hash():
    assert CONFIG.source == "visual_check_v1@1.1.0"
    assert CONFIG.path == "config/validate/visual_check_v1.json"
    assert len(CONFIG.sha256) == 64
    assert CONFIG.params.method == "color_seed_v1"
    assert CONFIG.params.other_colors == 3
    assert CONFIG.params.static_mean_abs_diff_max == 2.0


def test_segment_params_report_problems(tmp_path):
    config = json.loads(DEFAULT_CHECK_CONFIG.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="segmentation"):
        SegmentParams.from_config({k: v for k, v in config.items() if k != "segmentation"})
    config["segmentation"]["method"] = "sam2"
    with pytest.raises(ValueError, match="모르는 분할 방법"):
        SegmentParams.from_config(config)
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="읽지 못했습니다"):
        load_check_config(bad)


def test_mask_tools():
    mask = np.zeros((40, 40), bool)
    mask[10:30, 10:30] = True
    assert erode(mask, 3).sum() < mask.sum() < dilate(mask, 3).sum()
    assert erode(mask, 0).sum() == mask.sum()
    ring = mask.copy()
    ring[15:25, 15:25] = False  # 안쪽 구멍
    assert fill_holes(ring).sum() == mask.sum()
    edge = np.zeros((10, 10), bool)
    edge[:, :3] = True  # 화면 가장자리에 닿은 쪽은 줄이지 않는다
    assert erode(edge, 1)[:, 0].all()


def test_segmentation_finds_the_garment_but_not_the_trousers_or_skin():
    real, _ = vc.draw(vc.RENDER_SIZE, real=True, seed=3)
    _, render_mask = vc.draw(vc.RENDER_SIZE, real=False)
    found, info = segment_garment(real, render_mask, CONFIG.params)
    assert mask_iou(found, render_mask) > 0.95
    height = render_mask.shape[0]
    assert not found[round(0.75 * height) :].any()  # 옷 아래 어두운 바지는 빠진다
    assert len(info["other_lab"]) == 3  # 배경·피부·바지
    assert info["components_kept"] >= 1


@pytest.mark.parametrize(
    ("name", "status", "fails", "passed"),
    [
        ("same", PASS, set(), 11),
        ("recolor", FAIL, {"color_delta_e"}, 0),
        ("wider", FAIL, {"chest_width", "hem_width"}, 0),
        ("bad2", PASS, {"color_delta_e"}, 9),  # 2프레임 실패여도 9/11 통과
        ("bad4", FAIL, {"color_delta_e"}, 7),  # 4프레임 실패 → 9를 채울 수 없다
    ],
)
def test_reference_cases_are_judged_as_expected(clips, name, status, fails, passed):
    run = check_videos(clips[name], clips["render"], clips["mask"], CONFIG)
    result = run.result
    assert result.status == status
    assert {code for f in result.frames for code in f.fails} == fails
    assert result.frames_passed == passed
    assert len(result.frames) == 11
    assert len(run.real_masks) == 11


def test_same_garment_metrics_are_well_inside_the_limits(clips):
    metrics = check_videos(clips["same"], clips["render"], clips["mask"], CONFIG).result.metrics()
    assert metrics["silhouette_iou"] > 0.95
    assert metrics["color_delta_e"] < 2.0
    assert metrics["width_change_ratio"] < 0.02
    assert metrics["frames_passed"] == 11


def test_frames_are_matched_by_time_and_sampled_from_first_to_last(clips):
    run = check_videos(clips["same"], clips["render"], clips["mask"], CONFIG)
    assert [m.real_index for m in run.matches] == [0, 2, 5, 7, 10, 12, 14, 17, 19, 22, 24]
    assert [m.render_index for m in run.matches] == [m.real_index for m in run.matches]
    assert run.representative_pair() == (12, 12)  # 50 %
    record = run.to_record()
    assert record["real_video"]["width"] == vc.REAL_SIZE[0]
    assert record["details"]["render_mask"] == "static"
    assert len(record["segmentation"]) == 11


def test_per_frame_mask_function_is_used_for_moving_clips(clips):
    calls = []

    def per_frame(index, frame):
        calls.append(index)
        return clips["mask"]

    run = check_videos(clips["same"], clips["render"], per_frame, CONFIG)
    assert run.result.status == PASS
    assert len(calls) == 11
    assert run.result.details["render_mask"] == "per_frame"


def test_too_different_lengths_are_unverifiable(clips):
    root = clips["root"]
    longer = vc.real_clip(root / "longer.mp4", frames=41)  # 1.71초 vs 렌더 1.04초
    run = check_videos(longer, clips["render"], clips["mask"], CONFIG)
    assert run.result.status == UNVERIFIABLE
    assert "길이 차이" in run.result.blockers[0]
    assert run.result.frames == ()
    assert run.representative_pair() == (20, 20)  # 판정 없이도 가운데 프레임은 남긴다


def test_different_aspect_ratio_is_unverifiable_instead_of_stretched(clips):
    square = vc.real_clip(clips["root"] / "square.mp4", size=(240, 240))
    run = check_videos(square, clips["render"], clips["mask"], CONFIG)
    assert run.result.status == UNVERIFIABLE
    assert "화면비" in run.result.blockers[0]


def test_one_mask_for_a_moving_render_clip_is_unverifiable(clips):
    root = clips["root"]
    frames = [vc.draw(vc.RENDER_SIZE, real=False, widen=1.0 + i / 40)[0] for i in range(25)]
    moving = vc.write_clip(root / "moving.mp4", frames)
    run = check_videos(clips["same"], moving, clips["mask"], CONFIG)
    assert run.result.status == UNVERIFIABLE
    assert "마스크 한 장" in run.result.blockers[0]


@pytest.mark.parametrize(
    ("mask", "message"),
    [
        (np.ones((10, 10), bool), "크기"),
        (np.zeros((vc.RENDER_SIZE[1], vc.RENDER_SIZE[0]), bool), "비었습니다"),
    ],
)
def test_bad_render_masks_are_unverifiable(clips, mask, message):
    run = check_videos(clips["same"], clips["render"], mask, CONFIG)
    assert run.result.status == UNVERIFIABLE
    assert message in run.result.blockers[-1]


def test_unreadable_video_raises_value_error(clips):
    broken = clips["root"] / "broken.mp4"
    broken.write_bytes(b"not a video")
    with pytest.raises(ValueError):
        check_videos(broken, clips["render"], clips["mask"], CONFIG)


def test_contact_sheet_shows_eleven_frame_pairs(clips):
    run = check_videos(clips["wider"], clips["render"], clips["mask"], CONFIG)
    sheet = contact_sheet(run, height=120)
    width = round(vc.RENDER_SIZE[0] * 120 / vc.RENDER_SIZE[1])
    assert sheet.shape == (28 + 120 * 2 + 52, width * 11 + 4 * 10, 3)
    blocked = check_videos(
        vc.real_clip(clips["root"] / "long2.mp4", frames=41), clips["render"], clips["mask"], CONFIG
    )
    assert contact_sheet(blocked, height=120).shape[0] == 28 + 120 * 2 + 52  # 판정 없이도 그린다


def test_sleeves_reaching_the_chest_row_do_not_count_in_the_chest_width():
    """실사에서 팔이 올라가 소매가 가슴 줄까지 내려와도(몸통과 떨어져 있으면) 몸통만 잰다."""
    from dataclasses import replace

    from fitting.adapters.media.visual_measure import measure_frame
    from fitting.verify.frame_match import FrameMatch

    render = np.zeros((100, 80), dtype=bool)
    render[10:90, 25:55] = True  # 몸통만(가슴 줄 46행)
    real = render.copy()
    real[40:52, 5:20] = real[40:52, 60:75] = True  # 소매: 몸통에서 5px 떨어짐(높이 2 %는 2px)
    frame = np.zeros((100, 80, 3), np.uint8)
    match = FrameMatch(0, 0.0, 0, 0.0, "still", False)
    torso = measure_frame(frame, frame, render, real, CONFIG.criteria, fraction=0.0, match=match)
    assert torso.chest_change == 0.0 and torso.hem_change == 0.0
    full = replace(CONFIG.criteria, width_span="full")  # 1.0.0 방식이면 소매까지 잰다
    wide = measure_frame(frame, frame, render, real, full, fraction=0.0, match=match)
    assert wide.chest_change == pytest.approx(70 / 30 - 1, abs=1e-3)

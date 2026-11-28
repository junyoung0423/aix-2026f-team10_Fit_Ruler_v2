"""피팅룸 영상 검증 판정(순수 로직) 테스트."""

import ast
import json
from pathlib import Path

import pytest

from fitting.verify.fitroom_check import (
    FitRoomVideoCriteria,
    VideoMeasure,
    judge_fitroom,
)
from fitting.verify.visual_check import (
    FAIL,
    PASS,
    UNVERIFIABLE,
    FrameMeasure,
    VisualCriteria,
    judge_frame,
)

ROOT = Path(__file__).resolve().parents[2]
CONFIG = json.loads((ROOT / "config" / "validate" / "fitroom_check_v1.json").read_text("utf-8"))
VIDEO = FitRoomVideoCriteria.from_config(CONFIG, source="fitroom_check_v1@1.1.0")
FRAME = VisualCriteria.from_config(
    json.loads((ROOT / "config" / "validate" / "visual_check_v1.json").read_text("utf-8"))
)


def fit(**values):
    base = dict(fraction=0.0, real_index=0, real_time_s=0.0, render_index=0, render_time_s=0.0,
                render_area=1000, real_area=1000, iou=0.95, chest_change=0.01, hem_change=0.0,
                delta_e=3.0, common_ratio=0.9)
    base.update(values)
    return judge_frame(FrameMeasure.build(**base), FRAME)


def video(**values):
    base = dict(black=False, end_frame_gap=2.0, lightness=(20.0, 21.0, 22.5, 20.5), seconds=4.0,
                size=(720, 1280), jump_ratio=2.1, jump_at_s=1.2)
    base.update(values)
    return VideoMeasure(**base)


def test_criteria_come_from_the_experiments():
    assert VIDEO.end_frame_gap_max == 6.0  # 4초 1/4 바퀴 왕복 1.8~5.0 (1.0.0은 8초 한 바퀴 4.0)
    assert VIDEO.jump_ratio_max == 3.5  # 정상 1.6~3.0 · 장면이 끊긴 영상 4.0~4.3
    assert VIDEO.jump_median_floor == 2.0  # 실제 회전 영상 중앙값 3.1~4.4
    assert VIDEO.lightness_range_max == 6.0  # Veo 2.7~5.0 · LTX 6.4~8.9
    assert VIDEO.lightness_weight == 2.55
    assert VIDEO.garment_min_fraction == 0.01  # 9편 시점마다 옷 비율 최소 0.094
    assert VIDEO.to_record()["source"] == "fitroom_check_v1@1.1.0"
    assert VIDEO.to_record()["jump_ratio_max"] == 3.5
    with pytest.raises(ValueError, match="video"):
        FitRoomVideoCriteria.from_config({})
    broken = json.loads(json.dumps(CONFIG))
    del broken["video"]["end_frame_gap_max"]
    with pytest.raises(ValueError, match="end_frame_gap_max"):
        FitRoomVideoCriteria.from_config(broken)
    broken = json.loads(json.dumps(CONFIG))
    del broken["video"]["jump_ratio_max"]
    with pytest.raises(ValueError, match="jump_ratio_max"):
        FitRoomVideoCriteria.from_config(broken)


def test_good_fit_and_steady_video_pass_with_human_review_flag():
    result = judge_fitroom(fit(), video(), VIDEO)
    assert result.status == PASS
    assert result.human_review_required is True
    assert result.metrics() == {
        "quality_status": PASS,
        "color_delta_e": 3.0,
        "silhouette_iou": 0.95,
        "width_change_ratio": 0.01,
        "end_frame_gap": 2.0,
        "garment_lightness_range": 2.5,
        "frame_jump_ratio": 2.1,
    }
    record = result.to_record()
    assert record["side_back_fit_check"] == "not_automated"
    assert record["video"]["garment_missing_samples"] == 0
    assert record["video"]["jump_ratio"] == 2.1 and record["video"]["jump_at_s"] == 1.2
    assert record["fit_frame"]["status"] == PASS


@pytest.mark.parametrize(
    ("fit_values", "video_values", "fails"),
    [
        ({"iou": 0.7}, {}, ("fit_frame",)),
        ({}, {"end_frame_gap": 6.5}, ("end_frame_gap",)),
        ({}, {"lightness": (20.0, 12.5, 21.0)}, ("lightness_range",)),
        ({}, {"lightness": (20.0, 21.0), "garment_missing": 2}, ("garment_missing",)),
        ({}, {"jump_ratio": 4.3, "jump_at_s": 2.4}, ("frame_jump",)),
        ({}, {"black": True, "end_frame_gap": None, "lightness": (), "jump_ratio": None},
         ("black",)),
        ({"delta_e": 12.0}, {"end_frame_gap": 19.2}, ("fit_frame", "end_frame_gap")),
    ],
)
def test_measured_violations_fail(fit_values, video_values, fails):
    result = judge_fitroom(fit(**fit_values), video(**video_values), VIDEO)
    assert result.status == FAIL
    assert result.fails == fails
    assert result.human_review_required is False
    assert result.reasons()


def test_limits_are_inclusive():
    measured = video(end_frame_gap=6.0, lightness=(20.0, 26.0), jump_ratio=3.5)
    result = judge_fitroom(fit(), measured, VIDEO)
    assert result.status == PASS


def test_unmeasurable_fit_frame_is_unverifiable_unless_something_failed():
    unmeasurable = fit(real_area=0, iou=0.0)
    assert judge_fitroom(unmeasurable, video(), VIDEO).status == UNVERIFIABLE
    assert judge_fitroom(None, video(), VIDEO).unverifiable == ("no_fit_frame",)
    assert judge_fitroom(unmeasurable, video(end_frame_gap=9.0), VIDEO).status == FAIL


def test_logic_module_imports_no_external_sdk_or_adapter():
    source = (ROOT / "fitting" / "verify" / "fitroom_check.py").read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
    assert imported <= {"__future__", "collections", "dataclasses", "typing", "visual_check"}

"""D6 색·실루엣 검증 판정(순수 로직) 테스트. 영상·OpenCV 없이 numpy만 쓴다."""

import ast
import json
from pathlib import Path

import numpy as np
import pytest

from fitting.verify.visual_check import (
    FAIL,
    PASS,
    UNVERIFIABLE,
    FrameMeasure,
    VisualCriteria,
    band_width,
    box_height,
    delta_e_2000,
    judge_frame,
    judge_video,
    mask_center_x,
    mask_iou,
    median_lab,
    pick_representative,
    run_width,
    sample_indices,
    span_width,
    srgb_to_lab,
    torso_band_width,
    width_change,
    width_rows,
)

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "validate" / "visual_check_v1.json"
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
CRITERIA = VisualCriteria.from_config(CONFIG, source="visual_check_v1@1.0.0")


# -- 기준 ------------------------------------------------------------------


def test_criteria_are_the_spec_chapter_7_initial_values():
    c = CRITERIA
    assert c.sample_fractions == tuple(i / 10 for i in range(11))  # 0, 10, …, 100 % 11프레임
    assert c.max_duration_gap_s == 0.5
    assert c.silhouette_iou_min == 0.85
    assert c.width_change_max == 0.05
    assert c.color_delta_e_max == 8.0
    assert c.min_frames_passed == 9
    assert (c.chest_height, c.hem_height, c.width_rows) == (0.45, 0.9, 5)
    assert (c.area_ratio_min, c.area_ratio_max) == (0.5, 1.5)
    assert (c.color_erode_px, c.color_min_common_ratio) == (5, 0.2)
    # 1.1.0: 가슴·밑단 폭은 소매를 뺀 몸통 구간(높이 2 %보다 좁은 틈은 이어짐)
    assert (c.width_span, c.width_gap_ratio) == ("torso", 0.02)
    assert c.to_record()["width_span"] == "torso"
    assert c.to_record()["source"] == "visual_check_v1@1.0.0"


def test_criteria_report_missing_blocks_and_values():
    with pytest.raises(ValueError, match="블록이 없습니다: thresholds"):
        VisualCriteria.from_config({k: v for k, v in CONFIG.items() if k != "thresholds"})
    broken = json.loads(json.dumps(CONFIG))
    del broken["measurement"]["hem_height"]
    with pytest.raises(ValueError, match="measurement.hem_height"):
        VisualCriteria.from_config(broken)


@pytest.mark.parametrize(
    ("block", "key", "value", "message"),
    [
        ("sampling", "fractions", [0.5, 0.1], "오름차순"),
        ("sampling", "fractions", [0.0, 1.2], "0~1"),
        ("thresholds", "min_frames_passed", 12, "프레임 수 이하"),
        ("thresholds", "silhouette_iou_min", 0, "silhouette_iou_min"),
        ("measurement", "width_rows", 4, "홀수"),
        ("measurement", "chest_height", 0.95, "chest_height < hem_height"),
        ("measurement", "area_ratio_max", 0.9, "area_ratio_min < 1 < area_ratio_max"),
        ("measurement", "width_span", "both", "width_span"),
        ("measurement", "width_gap_ratio", 0.6, "width_gap_ratio"),
    ],
)
def test_criteria_reject_out_of_range_values(block, key, value, message):
    broken = json.loads(json.dumps(CONFIG))
    broken[block][key] = value
    with pytest.raises(ValueError, match=message):
        VisualCriteria.from_config(broken)


# -- 색 --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rgb", "lab"),
    [
        ((255, 255, 255), (100.0, 0.0, 0.0)),
        ((0, 0, 0), (0.0, 0.0, 0.0)),
        ((255, 0, 0), (53.2408, 80.0925, 67.2032)),
        ((128, 128, 128), (53.585, 0.0, 0.0)),  # C5 렌더 배경 #808080
    ],
)
def test_srgb_to_lab_matches_reference_values(rgb, lab):
    for array in (np.array(rgb, dtype=np.uint8), np.array(rgb, dtype=float)):
        assert srgb_to_lab(array) == pytest.approx(lab, abs=1e-3)


def test_srgb_to_lab_keeps_the_image_shape():
    image = np.zeros((4, 5, 3), dtype=np.uint8)
    assert srgb_to_lab(image).shape == (4, 5, 3)


@pytest.mark.parametrize(
    ("lab1", "lab2", "expected"),
    [  # Sharma, Wu, Dalal (2005) CIEDE2000 시험 자료 1, 7, 13, 17, 25, 34번
        ((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485), 2.0425),
        ((50.0, 0.0, 0.0), (50.0, -1.0, 2.0), 2.3669),
        ((50.0, 2.49, -0.001), (50.0, -2.49, 0.0009), 7.1792),
        ((50.0, 2.5, 0.0), (73.0, 25.0, -18.0), 27.1492),
        ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
        ((2.0776, 0.0795, -1.135), (0.9033, -0.0636, -0.5514), 0.9082),
    ],
)
def test_delta_e_2000_matches_the_sharma_test_data(lab1, lab2, expected):
    assert float(delta_e_2000(np.array(lab1), np.array(lab2))) == pytest.approx(expected, abs=1e-4)
    assert float(delta_e_2000(np.array(lab2), np.array(lab1))) == pytest.approx(expected, abs=1e-4)


def test_median_lab_uses_only_the_region():
    lab = np.zeros((2, 2, 3))
    lab[0, 0] = (10, 1, 2)
    lab[0, 1] = (30, 3, 4)
    region = np.array([[True, True], [False, False]])
    assert median_lab(lab, region) == (20.0, 2.0, 3.0)
    with pytest.raises(ValueError, match="빈 영역"):
        median_lab(lab, np.zeros((2, 2), bool))


# -- 실루엣 ----------------------------------------------------------------


def rect(shape, top, bottom, left, right) -> np.ndarray:
    mask = np.zeros(shape, dtype=bool)
    mask[top:bottom, left:right] = True
    return mask


def test_mask_iou():
    a = rect((10, 10), 0, 10, 0, 5)
    b = rect((10, 10), 0, 10, 0, 10)
    assert mask_iou(a, b) == pytest.approx(0.5)
    assert mask_iou(a, a) == 1.0
    assert mask_iou(np.zeros((3, 3), bool), np.zeros((3, 3), bool)) is None
    with pytest.raises(ValueError, match="크기가 다릅니다"):
        mask_iou(a, np.zeros((5, 5), bool))


def test_width_rows_are_45_and_90_percent_down_the_render_box():
    rows = width_rows(rect((200, 50), 20, 121, 10, 40), 0.45, 0.9)  # 상자 20~120행
    assert (rows.top, rows.bottom, rows.chest, rows.hem) == (20, 120, 65, 110)
    with pytest.raises(ValueError, match="비었습니다"):
        width_rows(np.zeros((5, 5), bool), 0.45, 0.9)


def test_band_width_is_the_median_extent_over_five_rows():
    mask = rect((20, 30), 0, 20, 5, 15)  # 폭 10
    mask[9, 0:30] = True  # 한 행만 넓어도 중앙값은 그대로
    mask[11, :] = False  # 한 행이 비어도(폭 0) 그대로
    assert band_width(mask, 10, 5) == 10.0
    assert band_width(mask, 10, 1) == 10.0
    assert band_width(mask, 25, 5) is None  # 화면 밖


def test_width_change_and_box_height():
    assert width_change(100, 106) == pytest.approx(0.06)
    assert width_change(0, 10) is None
    assert width_change(10, None) is None
    assert box_height(rect((30, 5), 4, 24, 0, 5)) == 20
    assert box_height(np.zeros((3, 3), bool)) == 0


def test_sample_indices_cover_the_video_from_first_to_last_frame():
    fractions = CRITERIA.sample_fractions
    assert sample_indices(121, fractions) == [0, 12, 24, 36, 48, 60, 72, 84, 96, 108, 120]
    assert sample_indices(65, fractions) == [0, 6, 13, 19, 26, 32, 38, 45, 51, 58, 64]
    with pytest.raises(ValueError):
        sample_indices(0, fractions)


# -- 판정 ------------------------------------------------------------------


def measure(fraction=0.5, **values) -> FrameMeasure:
    base = dict(
        fraction=fraction,
        real_index=round(fraction * 120),
        real_time_s=round(fraction * 5, 4),
        render_index=round(fraction * 120),
        render_time_s=round(fraction * 5, 4),
        render_area=1000,
        real_area=1000,
        iou=0.95,
        chest_change=0.01,
        hem_change=-0.01,
        delta_e=3.0,
        common_ratio=0.9,
    )
    base.update(values)
    return FrameMeasure.build(**base)


def test_frame_passes_inside_every_limit_and_limits_are_inclusive():
    assert judge_frame(measure(), CRITERIA).status == PASS
    edge = measure(iou=0.85, chest_change=0.05, hem_change=-0.05, delta_e=8.0)
    assert judge_frame(edge, CRITERIA).status == PASS


@pytest.mark.parametrize(
    ("values", "code"),
    [
        ({"iou": 0.849}, "silhouette_iou"),
        ({"chest_change": 0.051}, "chest_width"),
        ({"hem_change": -0.06}, "hem_width"),
        ({"delta_e": 8.01}, "color_delta_e"),
    ],
)
def test_frame_fails_on_each_measured_violation(values, code):
    result = judge_frame(measure(**values), CRITERIA)
    assert result.status == FAIL
    assert result.fails == (code,)


def test_judgement_uses_the_recorded_rounded_values():
    # 0.8496은 기록(소수 셋째 자리)에서 0.85가 되고, 화면 숫자와 같게 통과로 본다
    assert measure(iou=0.8496).iou == 0.85
    assert judge_frame(measure(iou=0.8496), CRITERIA).status == PASS
    assert measure(delta_e=8.004).delta_e == 8.0


@pytest.mark.parametrize(
    ("values", "code"),
    [
        ({"real_area": 0, "iou": 0.0}, "no_garment"),
        ({"real_area": 499}, "area_ratio"),
        ({"real_area": 1501}, "area_ratio"),
    ],
)
def test_frame_is_unmeasurable_when_the_garment_area_is_out_of_range(values, code):
    # 면적이 허용 배율 밖이면 다른 값이 나빠도 실패가 아니라 측정 불가다(명세서 7장)
    result = judge_frame(measure(delta_e=30.0, **values), CRITERIA)
    assert result.status == UNVERIFIABLE
    assert result.unverifiable == (code,)
    assert result.fails == ()


def test_frame_with_too_small_color_area_cannot_pass_but_can_fail():
    no_color = judge_frame(measure(delta_e=None, common_ratio=0.1), CRITERIA)
    assert no_color.status == UNVERIFIABLE
    assert no_color.unverifiable == ("color_area",)
    also_bad = judge_frame(measure(delta_e=None, common_ratio=0.1, iou=0.5), CRITERIA)
    assert also_bad.status == FAIL


def video(statuses: str) -> list[FrameMeasure]:
    """P(통과)·F(색 실패)·U(옷 못 찾음) 문자열로 11프레임 측정값을 만든다."""
    values = {"P": {}, "F": {"delta_e": 20.0}, "U": {"real_area": 0, "iou": 0.0}}
    return [measure(fraction=i / 10, **values[s]) for i, s in enumerate(statuses)]


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ("PPPPPPPPPPP", PASS),
        ("FPPPPPPPPPF", PASS),  # 9/11 통과
        ("FUPPPPPPPPU", UNVERIFIABLE),  # 8 통과, 실패 1 → 측정 불가 때문에 모자람
        ("FFPPPPPPPPF", FAIL),  # 실패 3이면 9를 채울 수 없다
        ("UUUUUUUUUUU", UNVERIFIABLE),
        ("UUPPPPPPPPP", PASS),
    ],
)
def test_video_needs_nine_passing_frames_and_fails_only_on_measured_violations(statuses, expected):
    result = judge_video(video(statuses), CRITERIA)
    assert result.status == expected
    assert result.frames_passed == statuses.count("P")
    assert result.frames_failed == statuses.count("F")
    assert result.frames_unverifiable == statuses.count("U")


def test_blockers_make_the_video_unverifiable():
    result = judge_video(video("P" * 11), CRITERIA, blockers=["두 영상 길이 차이가 큽니다."])
    assert result.status == UNVERIFIABLE
    assert result.reasons()[0] == "두 영상 길이 차이가 큽니다."
    assert judge_video([], CRITERIA).status == UNVERIFIABLE


def test_representative_is_the_passing_frame_nearest_the_middle():
    passing_middle = judge_video(video("PPPPPPPPPPP"), CRITERIA)
    assert passing_middle.representative_frame.measure.fraction == 0.5

    # 가운데(50 %)가 실패하면 40 %와 60 % 중 이른 40 %
    tie = judge_video(video("PPPPPFPPPPP"), CRITERIA)
    assert tie.status == PASS
    assert tie.representative_frame.measure.fraction == 0.4
    assert tie.to_record()["representative"]["passed"] is True

    # PASS가 아니면 사람이 볼 수 있게 가운데 프레임(통과 프레임이 아님)
    failed = judge_video(video("FFFFFFFFFFF"), CRITERIA)
    assert failed.representative_frame.measure.fraction == 0.5
    assert failed.to_record()["representative"]["passed"] is False
    assert pick_representative((), PASS) is None


def test_metrics_are_medians_of_measured_frames_with_contract_keys():
    frames = [measure(fraction=i / 10, iou=0.90 + i / 100, delta_e=2.0 + i / 2) for i in range(11)]
    frames[0] = measure(fraction=0.0, real_area=0, iou=0.0, delta_e=None)  # 측정 불가는 뺀다
    metrics = judge_video(frames, CRITERIA).metrics()
    assert metrics == {
        "quality_status": PASS,
        "color_delta_e": 4.75,
        "silhouette_iou": 0.955,
        "width_change_ratio": 0.01,
        "frames_passed": 10,
    }
    assert judge_video([], CRITERIA).metrics() == {"quality_status": UNVERIFIABLE}


def test_record_keeps_method_criteria_and_human_review_flag():
    record = judge_video(video("PPPPPPPPPPP"), CRITERIA).to_record()
    assert record["check_version"] == "visual_check_v1"
    assert record["criteria"]["silhouette_iou_min"] == 0.85
    assert len(record["frames"]) == 11
    assert {"silhouette_iou", "chest_width_change", "color_delta_e"} <= set(record["frames"][0])
    # 중대한 변형은 자동으로 찾지 않으므로 PASS에도 사람 확인 표시(명세서 7장)
    assert record["human_review_required"] is True
    assert record["deformation_check"] == "not_automated"
    assert judge_video(video("F" * 11), CRITERIA).to_record()["human_review_required"] is False


def test_logic_module_imports_no_external_sdk_or_adapter():
    source = (ROOT / "fitting" / "verify" / "visual_check.py").read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__", "math", "statistics", "collections", "dataclasses",
                        "typing", "numpy"}  # fmt: skip


# -- 몸통 폭(소매 빼기, 설정 1.1.0) -------------------------------------------


def test_criteria_without_width_span_measure_the_full_extent_like_1_0_0():
    old = json.loads(json.dumps(CONFIG))
    del old["measurement"]["width_span"], old["measurement"]["width_gap_ratio"]
    criteria = VisualCriteria.from_config(old)
    assert (criteria.width_span, criteria.width_gap_ratio) == ("full", 0.0)


def body_with_sleeves(gap: int) -> np.ndarray:
    """가운데 몸통(20~39열, 폭 20)과, 10~19행에 몸통에서 `gap`픽셀 떨어진 소매(폭 8)."""
    mask = rect((40, 80), 0, 40, 20, 40)
    mask[10:20, 20 - gap - 8 : 20 - gap] = True
    mask[10:20, 40 + gap : 40 + gap + 8] = True
    return mask


def test_run_width_takes_the_run_through_the_center_or_the_nearest_one():
    line = np.zeros(30, dtype=bool)
    line[2:6] = line[10:20] = line[24:27] = True
    assert run_width(line, 15) == 10
    assert run_width(line, 7) == 4  # 빈 열: 2~5까지 2, 10~19까지 3
    assert run_width(line, 8) == 10  # 2~5까지 3, 10~19까지 2
    assert run_width(line, 15, max_gap=3) == 10  # 틈 4px는 끊는다
    assert run_width(line, 15, max_gap=4) == 25  # 틈 4px까지 이어 2~26
    assert run_width(np.zeros(5, dtype=bool), 2) == 0


def test_torso_width_leaves_out_sleeves_apart_from_the_body():
    mask = body_with_sleeves(gap=6)
    assert mask_center_x(mask) == 30
    assert span_width(mask, 15, 5, "full") == band_width(mask, 15, 5) == 48.0  # 소매까지
    assert span_width(mask, 15, 5, "torso") == 20.0  # 몸통만
    assert span_width(mask, 30, 5, "torso") == span_width(mask, 30, 5, "full") == 20.0
    assert torso_band_width(mask, 50, 5, 30) is None  # 화면 밖


def test_torso_width_joins_gaps_narrower_than_the_gap_ratio():
    narrow = body_with_sleeves(gap=2)
    assert span_width(narrow, 15, 5, "torso") == 20.0  # 비율 0이면 틈에서 끊는다
    # 높이 40의 5 % = 2px까지 이으면 소매까지 10~49열
    assert span_width(narrow, 15, 5, "torso", gap_ratio=0.05) == 40.0
    assert span_width(body_with_sleeves(gap=6), 15, 5, "torso", gap_ratio=0.05) == 20.0


def test_span_width_rejects_an_unknown_span_and_handles_an_empty_mask():
    with pytest.raises(ValueError, match="폭 재는 방식"):
        span_width(body_with_sleeves(gap=6), 15, 5, "both")
    assert mask_center_x(np.zeros((5, 5), dtype=bool)) is None
    assert span_width(np.zeros((5, 5), dtype=bool), 2, 3, "torso") == 0.0

"""견본집 생성 계획 규칙 (#4): ID·style 구간·번호."""

import copy
from pathlib import Path

import pytest
import yaml

from fitting.catalog.sample_plan import (
    SamplePlanError,
    check_sample_id,
    parse_sample_plan,
    parse_style_bands,
    style_for_chest,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / "config" / "samples" / "tee_gc_v1.yaml"
BODY_CHEST = 99.8


@pytest.fixture
def plan_data() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


@pytest.fixture
def bands(plan_data) -> list:
    return parse_style_bands(plan_data["style_bands"])


def test_committed_config_is_valid(plan_data):
    plan = parse_sample_plan(plan_data)
    ids = [s["id"] for s in plan["samples"]]
    assert len(ids) == 12
    assert ids[0] == "tee_gc_slim_01"
    assert ids[-1] == "tee_gc_oversized_12"
    assert plan["generation_body_chest_cm"] == BODY_CHEST


@pytest.mark.parametrize(
    ("ease", "style"),
    [
        (0.2, "slim"),  # 100 cm 견본
        (5.99, "slim"),
        (6.0, "regular"),  # 경계: 6 이상은 regular
        (14.0, "regular"),  # 경계: 14 이하는 regular
        (14.01, "relaxed"),
        (22.0, "relaxed"),  # 경계: 22 이하는 relaxed
        (22.01, "oversized"),
        (26.2, "oversized"),  # 126 cm 견본
    ],
)
def test_style_band_boundaries(bands, ease, style):
    assert style_for_chest(BODY_CHEST + ease, BODY_CHEST, bands) == style


def test_style_band_gap_returns_none():
    bands = parse_style_bands(
        [
            {
                "style": "a",
                "lower": None,
                "upper": 5.0,
                "include_lower": False,
                "include_upper": False,
            },
            {
                "style": "b",
                "lower": 5.0,
                "upper": None,
                "include_lower": False,
                "include_upper": False,
            },
        ]
    )
    assert style_for_chest(105.0, 100.0, bands) is None  # 여유 5.0은 어느 구간에도 없다


@pytest.mark.parametrize(
    ("sample_id", "style", "ok"),
    [
        ("tee_gc_slim_01", "slim", True),
        ("tee_gc_slim_1", "slim", False),
        ("tee_gc_Slim_01", "slim", False),
        ("tee_slim_01", "slim", False),
        ("tee_gc_regular_01", "slim", False),
    ],
)
def test_sample_id(sample_id, style, ok):
    assert (check_sample_id(sample_id, style) == []) is ok


def _problems(data) -> str:
    with pytest.raises(SamplePlanError) as info:
        parse_sample_plan(data)
    return " | ".join(info.value.problems)


def test_wrong_style_for_chest_is_rejected(plan_data):
    data = copy.deepcopy(plan_data)
    data["samples"][4]["chest_girth_cm"] = 104.0  # regular_05 → 여유 4.2 cm는 slim
    data["samples"][4]["body_length_cm"] = 69.0
    assert "style은 slim인데 regular" in _problems(data)


def test_wrong_number_is_rejected(plan_data):
    data = copy.deepcopy(plan_data)
    data["samples"][0]["id"] = "tee_gc_slim_07"
    assert "번호는" in _problems(data)


def test_duplicate_id_is_rejected(plan_data):
    data = copy.deepcopy(plan_data)
    data["samples"][1]["id"] = data["samples"][0]["id"]
    assert "겹치는 ID" in _problems(data)


def test_missing_target_is_rejected(plan_data):
    data = copy.deepcopy(plan_data)
    data["samples"][0]["sleeve_length_cm"] = None
    assert "양수 cm가 아닌 목표" in _problems(data)


def test_unknown_key_is_rejected(plan_data):
    data = copy.deepcopy(plan_data)
    data["samples"][0]["shoulder_width_cm"] = 40.0  # 어깨는 맞추지 않는다
    assert "키는" in _problems(data)

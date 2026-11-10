"""신체 측정 위치(measure_v1): decisions.md 표, 필드 설명, 스키마가 명세서 원문과 같은지 (#1)."""

import json
import re

import pytest
from pydantic import ValidationError

from fitting.domain import Measurements

from .conftest import REPO_ROOT

# 명세서 3장 표 원문
SPEC = {
    "height_cm": "맨발로 선 상태의 바닥부터 정수리까지 수직 높이",
    "chest_cm": "가슴이 가장 돌출된 수평 위치의 둘레. 줄자를 당기지 않음",
    "waist_cm": "자연 허리 위치의 수평 둘레. 배꼽 둘레와 구분",
    "shoulder_cm": "좌우 어깨끝 기준점 사이의 직선 거리. 몸 표면을 따른 곡선 길이와 구분",
    "arm_length_cm": (
        "오른쪽 어깨끝에서 팔꿈치를 거쳐 손목 기준점까지. 약하게 굽힌 표준 측정 자세 사용"
    ),
}


def _decisions_table() -> dict[str, str]:
    text = (REPO_ROOT / "docs" / "decisions.md").read_text(encoding="utf-8")
    section = text.split("## 신체 측정 위치 (measure_v1)", 1)[1].split("\n## ", 1)[0]
    return dict(re.findall(r"^\| (\w+_cm) \| (.+?) \|$", section, flags=re.M))


def test_decisions_table_is_the_spec_text():
    assert _decisions_table() == SPEC


def test_field_descriptions_are_the_spec_text():
    assert {name: f.description for name, f in Measurements.model_fields.items()} == SPEC


@pytest.mark.parametrize("schema", ["fitting_request", "body_input"])
def test_schema_shows_the_positions(schema):
    data = json.loads(
        (REPO_ROOT / "docs" / "api" / "schemas" / f"{schema}.schema.json").read_text(
            encoding="utf-8"
        )
    )
    props = data["$defs"]["Measurements"]["properties"] if "$defs" in data else data["properties"]
    for name, text in SPEC.items():
        assert props[name]["description"] == text


def test_descriptions_did_not_change_range_or_strictness():
    base = dict(
        height_cm=175.0, chest_cm=100.0, waist_cm=80.0, shoulder_cm=44.0, arm_length_cm=60.0
    )
    Measurements(**{**base, "height_cm": 140.0})
    for bad in ({"height_cm": 139.9}, {"chest_cm": "100"}, {"waist_cm": True}):
        with pytest.raises(ValidationError):
            Measurements(**{**base, **bad})

"""AI Hub 71501 라벨 -> GarmentVariant 변환 (#3, C1). 가짜 라벨만 쓴다."""

import json
import re

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from fitting.adapters.aihub import SOURCE, AihubLabelRejected, garment_id, label_to_garment
from fitting.adapters.aihub.tables import AIHUB_COLOR, AIHUB_FIBER, COLOR_HEX
from fitting.domain import FabricInfo, GarmentStatus, GarmentVariant

from .aihub_fake import FAKE_PRODUCT_ID, fake_label
from .conftest import EXAMPLES, REPO_ROOT

SCHEMA = json.loads(
    (REPO_ROOT / "docs" / "api" / "schemas" / "garment_variant.schema.json").read_text(
        encoding="utf-8"
    )
)


def _reasons(**clothes) -> list[str]:
    with pytest.raises(AihubLabelRejected) as caught:
        label_to_garment(fake_label(**clothes))
    return caught.value.reasons


# --- 정상 변환 -------------------------------------------------------------------------------


def test_converts_a_short_sleeve_round_neck_label():
    garment = label_to_garment(fake_label())

    assert garment.id == f"tee_unknown_unknown_white_aihub_{FAKE_PRODUCT_ID}"
    assert (garment.style, garment.size, garment.color) == ("unknown", "unknown", "white")
    assert garment.color_hex == COLOR_HEX["white"]
    assert garment.status is GarmentStatus.READY
    assert garment.garment_family == "basic_short_sleeve_crew"
    assert garment.fabric_preset == "cotton_jersey_v0"
    assert garment.source_ref == FAKE_PRODUCT_ID

    m = garment.measurements
    assert (m.chest_girth_cm, m.waist_girth_cm, m.shoulder_width_cm, m.sleeve_length_cm) == (
        110.0,
        104.0,
        47.0,
        21.0,
    )
    assert m.body_length_cm is None  # 앞길이 기준점이 HPS인지 알 수 없다

    meta = garment.measurement_meta
    assert meta.source == SOURCE == "AI Hub 의류 통합 데이터(dataSetSn 71501)"
    assert meta.is_measured is True
    assert meta.measurement_protocol_version == "measure_v1"
    assert meta.chest_from_flat_width is False
    assert meta.waist_from_flat_width is False
    assert meta.waist_position_matched is False
    assert "앞길이 원래 값 70.0 cm" in meta.notes
    assert "착용 모델 평소 사이즈 L(의류 표기 사이즈 아님)" in meta.notes
    assert "한국지능정보사회진흥원" in meta.notes  # 이용정책: 2차적 저작물에도 사업결과임을 밝힌다
    assert not garment.is_demo

    assert garment.fabric == FabricInfo(
        fibers=["cotton"],
        composition=None,
        stretch="contain little",
        description_en="plain white cotton jersey",
    )


def test_id_uses_only_lowercase_letters_digits_and_underscores():
    garment = label_to_garment(fake_label(color="Beige"))
    assert re.fullmatch(r"[a-z0-9_]+", garment.id)
    assert garment.id == garment_id("beige", FAKE_PRODUCT_ID)


def test_does_not_use_broken_path_strings():
    label = fake_label()
    label["dataset"]["dataset.image_path"] = "./��/broken.jpg"  # 깨진 한글 흉내
    del label["dataset"]["dataset.label_path"]
    assert label_to_garment(label).source_ref == FAKE_PRODUCT_ID


def test_converted_garment_passes_the_json_schema():
    garment = label_to_garment(fake_label())
    Draft202012Validator(SCHEMA).validate(garment.model_dump(mode="json"))


# --- 단면 -> 둘레 ---------------------------------------------------------------------------


def test_flat_width_option_doubles_chest_and_waist_and_sets_flags():
    garment = label_to_garment(
        fake_label(chest_size="54.0", waist_size="50.5"), chest_waist_flat_width=True
    )
    assert garment.measurements.chest_girth_cm == 108.0
    assert garment.measurements.waist_girth_cm == 101.0
    assert garment.measurements.shoulder_width_cm == 47.0  # 다른 치수는 그대로
    assert garment.measurement_meta.chest_from_flat_width is True
    assert garment.measurement_meta.waist_from_flat_width is True
    assert "2배" in garment.measurement_meta.notes


def test_flat_width_flag_is_false_for_a_missing_waist():
    garment = label_to_garment(
        fake_label(chest_size="54.0", waist_size="null"), chest_waist_flat_width=True
    )
    assert garment.measurements.waist_girth_cm is None
    assert garment.measurement_meta.chest_from_flat_width is True
    assert garment.measurement_meta.waist_from_flat_width is False


def test_flat_width_range_applies_to_the_doubled_value():
    # 둘레 범위 가슴 60~200 cm -> 단면 원래 값은 30~100 cm (허리는 50 cm 단면으로 고정)
    for ok in ("30.0", "100.0"):
        label_to_garment(fake_label(chest_size=ok, waist_size="50.0"), chest_waist_flat_width=True)
    for bad in ("29.9", "100.1"):
        with pytest.raises(AihubLabelRejected, match="chest_size"):
            label_to_garment(
                fake_label(chest_size=bad, waist_size="50.0"), chest_waist_flat_width=True
            )
    # 둘레로 보면 정상인 허리 104 cm도 단면으로 보면 둘레 208 cm라 범위 밖이다
    with pytest.raises(AihubLabelRejected, match="waist_size"):
        label_to_garment(fake_label(chest_size="54.0"), chest_waist_flat_width=True)


# --- 필수 치수 누락 -> DRAFT -----------------------------------------------------------------


@pytest.mark.parametrize(
    "missing",
    [
        {"chest_size": "null"},
        {"chest_size": None},
        {"shoulder_width": "null"},
        {"shoulder_width": "0"},
        {"chest_size": "", "shoulder_width": "null"},
    ],
)
def test_missing_chest_or_shoulder_is_draft(missing):
    garment = label_to_garment(fake_label(**missing))
    assert garment.status is GarmentStatus.DRAFT
    Draft202012Validator(SCHEMA).validate(garment.model_dump(mode="json"))


def test_missing_optional_measurements_are_null_not_zero():
    garment = label_to_garment(fake_label(sleeve_length="0.0", waist_size="null"))
    assert garment.status is GarmentStatus.READY
    assert garment.measurements.sleeve_length_cm is None
    assert garment.measurements.waist_girth_cm is None


def test_missing_front_length_is_not_in_notes():
    garment = label_to_garment(fake_label(front_length="null"))
    assert "앞길이" not in garment.measurement_meta.notes


# --- 반팔 라운드넥이 아닌 것 거절 --------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"sleeve_length_type": "long sleeves"}, "sleeve_length_type"),
        ({"sleeve_length_type": "sleeveless"}, "sleeve_length_type"),
        ({"topneck_color_design": "polo collar"}, "topneck_color_design"),
        ({"topneck_color_design": "V-neck"}, "topneck_color_design"),
        ({"type": "02top_03sweater"}, "type"),
        ({"topneck_color_design": None}, "topneck_color_design"),
    ],
)
def test_rejects_labels_that_are_not_short_sleeve_round_neck_tees(change, field):
    reasons = _reasons(**change)
    assert any("반팔 라운드넥 티셔츠가 아닙니다" in r and field in r for r in reasons)


def test_rejection_lists_every_reason():
    reasons = _reasons(sleeve_length_type="long sleeves", chest_size="-1", color="Black,White")
    assert len(reasons) == 3


def test_rejects_a_label_without_a_product_number():
    label = fake_label()
    label["dataset"]["dataset.name"] = "not_a_label_name"
    with pytest.raises(AihubLabelRejected, match="상품 번호"):
        label_to_garment(label)


def test_rejects_a_label_without_clothes_metadata():
    label = fake_label()
    del label["metadata.clothes"]
    with pytest.raises(AihubLabelRejected):
        label_to_garment(label)


# --- 단위 오류 거절 ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "text"),
    [
        ({"chest_size": "1100.0"}, "mm"),  # mm로 적은 가슴둘레
        ({"shoulder_width": "470"}, "mm"),
        ({"chest_size": "-108.0"}, "음수"),
        ({"sleeve_length": "-1"}, "음수"),
        ({"shoulder_width": "abc"}, "숫자가 아닙니다"),
        ({"chest_size": True}, "숫자가 아닙니다"),
        ({"chest_size": "nan"}, "유한한"),
        ({"shoulder_width": "11.0"}, "범위"),  # 너무 작은 값
    ],
)
def test_rejects_unit_errors(change, text):
    reasons = _reasons(**change)
    assert len(reasons) == 1 and text in reasons[0]


@pytest.mark.parametrize(
    ("field", "low", "high"),
    [
        ("chest_size", 60.0, 200.0),
        ("waist_size", 50.0, 200.0),
        ("shoulder_width", 25.0, 80.0),
        ("sleeve_length", 5.0, 45.0),
        ("front_length", 35.0, 100.0),
    ],
)
def test_range_boundaries_are_inclusive(field, low, high):
    for ok in (low, high):
        label_to_garment(fake_label(**{field: str(ok)}))
    for bad in (round(low - 0.1, 1), round(high + 0.1, 1)):
        with pytest.raises(AihubLabelRejected, match=field):
            label_to_garment(fake_label(**{field: str(bad)}))


def test_accepts_numbers_as_well_as_numeric_strings():
    garment = label_to_garment(fake_label(chest_size=110, shoulder_width=47.5))
    assert garment.measurements.chest_girth_cm == 110.0
    assert garment.measurements.shoulder_width_cm == 47.5


# --- 색 대응표 -------------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(AIHUB_COLOR))
def test_every_aihub_color_maps_to_a_lowercase_key_with_a_hex(name):
    garment = label_to_garment(fake_label(color=name))
    assert garment.color == name.lower()
    assert re.fullmatch(r"[a-z]+", garment.color)
    assert re.fullmatch(r"#[0-9a-f]{6}", garment.color_hex)
    assert garment.color_hex == COLOR_HEX[garment.color]
    assert garment.fabric.description_en == f"plain {garment.color} cotton jersey"


def test_color_table_covers_the_dataset_color_list():
    # 데이터 구조표 4-7의 색상 목록(Mixed 제외)
    dataset_colors = {
        "Black", "White", "Gray", "Red", "Orange", "Pink", "Yellow",
        "Brown", "Green", "Blue", "Purple", "Beige",
    }  # fmt: skip
    assert set(AIHUB_COLOR) == dataset_colors
    assert set(AIHUB_COLOR.values()) <= set(COLOR_HEX)


def test_hex_values_match_the_screen_table_for_shared_keys():
    # web/src/labels.ts의 임시 표와 같은 값 (화면과 견본 색이 어긋나지 않게)
    assert COLOR_HEX["navy"] == "#24324f"
    assert COLOR_HEX["black"] == "#222326"
    assert COLOR_HEX["white"] == "#f4f4f2"
    assert COLOR_HEX["gray"] == "#8d9096"


@pytest.mark.parametrize(("raw", "key"), [("gray", "gray"), ("PINK", "pink"), (" White ", "white")])
def test_color_names_ignore_case(raw, key):
    assert label_to_garment(fake_label(color=raw)).color == key


def test_fiber_names_ignore_case():
    garment = label_to_garment(fake_label(fiber_composition="Regenerated fiber Others,cotton"))
    assert garment.fabric.fibers == ["regenerated_other", "cotton"]


@pytest.mark.parametrize("color", ["Black,White", "Mixed", "Navy", "Orenge", "null", 3])
def test_rejects_multi_unknown_or_missing_colors(color):
    reasons = _reasons(color=color)
    assert len(reasons) == 1 and "색" in reasons[0]


# --- 원단 ------------------------------------------------------------------------------------


def test_fabric_keeps_fiber_order_and_stretch_as_is():
    garment = label_to_garment(
        fake_label(fiber_composition="Polyester,Cotton,Polyurethane", elasticity="contain a lot")
    )
    assert garment.fabric.fibers == ["polyester", "cotton", "polyurethane"]
    assert garment.fabric.composition is None
    assert garment.fabric.stretch == "contain a lot"
    assert garment.fabric.description_en == "plain white polyester-cotton-polyurethane blend jersey"
    assert "면 단독이 아니다" in garment.measurement_meta.notes


def test_every_dataset_fiber_name_is_mapped():
    for name, (key, words) in AIHUB_FIBER.items():
        garment = label_to_garment(fake_label(fiber_composition=name))
        assert garment.fabric.fibers == [key]
        assert re.fullmatch(r"[a-z_]+", key)
        assert words in garment.fabric.description_en


def test_missing_fiber_and_stretch_are_kept_empty():
    garment = label_to_garment(fake_label(fiber_composition="null", elasticity=None))
    assert garment.fabric.fibers == []
    assert garment.fabric.stretch is None
    assert garment.fabric.description_en == "plain white jersey"


@pytest.mark.parametrize(
    ("change", "text"),
    [({"fiber_composition": "Cotton,Kevlar"}, "섬유"), ({"elasticity": "very"}, "신축성")],
)
def test_rejects_unknown_fabric_values(change, text):
    reasons = _reasons(**change)
    assert len(reasons) == 1 and text in reasons[0]


# --- 계약: 새 필드 ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [EXAMPLES / "garment.json", REPO_ROOT / "data" / "garments" / "tee_regular_m_navy_v1.json"],
)
def test_examples_with_new_fields_pass_the_schema(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["color_hex"] and data["fabric"]["description_en"]
    Draft202012Validator(SCHEMA).validate(data)
    GarmentVariant.model_validate(data)


def test_new_fields_are_optional_for_older_garment_files():
    data = json.loads((EXAMPLES / "garment.json").read_text(encoding="utf-8"))
    del data["color_hex"], data["fabric"]
    Draft202012Validator(SCHEMA).validate(data)
    garment = GarmentVariant.model_validate(data)
    assert garment.color_hex is None and garment.fabric is None


@pytest.mark.parametrize("bad", ["#24324F", "24324f", "#2432", "navy"])
def test_color_hex_must_be_lowercase_rrggbb(bad):
    data = json.loads((EXAMPLES / "garment.json").read_text(encoding="utf-8"))
    data["color_hex"] = bad
    with pytest.raises(ValidationError):
        GarmentVariant.model_validate(data)
    assert list(Draft202012Validator(SCHEMA).iter_errors(data))


@pytest.mark.parametrize("percent", [0, -1, 100.1, "50"])
def test_fabric_composition_is_a_percent(percent):
    with pytest.raises(ValidationError):
        FabricInfo(composition={"cotton": percent})


def test_fabric_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        FabricInfo.model_validate({"fibers": [], "weight_gsm": 180})

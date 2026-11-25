"""실사화 프롬프트 템플릿(D5) 테스트. fal을 부르지 않는다."""

from pathlib import Path

import pytest

from fitting.adapters.fal_ltx.prompt import (
    PROMPT_VERSION,
    SUBJECTS,
    build_prompt,
    garment_description,
    material_description,
)
from fitting.domain import FabricInfo, GarmentVariant

ROOT = Path(__file__).resolve().parents[2]


def load(name: str) -> GarmentVariant:
    path = ROOT / "data" / "garments" / f"{name}.json"
    return GarmentVariant.model_validate_json(path.read_text(encoding="utf-8"))


NAVY = load("tee_regular_m_navy_v1")
BLACK = load("tee_relaxed_l_black_v1")


def variant(**changes) -> GarmentVariant:
    return NAVY.model_copy(update=changes)


# --- 옷 설명: 색·원단·핏 ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("garment", "expected"),
    [
        (NAVY, "plain navy cotton jersey short-sleeve crew-neck T-shirt with a regular fit"),
        (BLACK, "plain black cotton jersey short-sleeve crew-neck T-shirt with a relaxed fit"),
    ],
)
def test_demo_garments_describe_color_fabric_and_fit(garment, expected):
    assert garment_description(garment) == expected


def test_color_is_added_when_the_fabric_text_has_no_color_word():
    garment = variant(fabric=FabricInfo(fibers=["cotton"], description_en="soft cotton jersey."))
    assert material_description(garment) == "navy soft cotton jersey"


def test_color_word_is_matched_as_a_whole_word():
    # "navy"가 다른 낱말 안에만 있으면 색이 없는 것으로 본다
    garment = variant(fabric=FabricInfo(description_en="navyish cotton jersey"))
    assert material_description(garment) == "navy navyish cotton jersey"


def test_fibers_are_used_when_there_is_no_fabric_text():
    garment = variant(color="gray", fabric=FabricInfo(fibers=["cotton", "polyester"]))
    assert material_description(garment) == "plain gray cotton-polyester blend jersey"


def test_without_fabric_it_falls_back_to_cotton_like_d4():
    assert material_description(variant(fabric=None)) == "plain navy cotton jersey"


def test_unknown_color_and_style_are_left_out():
    garment = variant(color="unknown", style="unknown", fabric=None)
    assert garment_description(garment) == "plain cotton jersey short-sleeve crew-neck T-shirt"


# --- 문장: 얼굴 처리·배경·보존 ------------------------------------------------------------------


def test_prompt_keeps_the_head_out_of_frame_without_naming_the_face():
    text = build_prompt(NAVY).text.lower()
    assert "front view from the neck down" in text  # decisions.md 2절
    assert "cropped below the chin" in text
    # 부정문으로 얼굴·머리를 부르지 않는다
    for word in ("face", "head", "hair"):
        assert word not in text


def test_prompt_matches_the_render_background_and_asks_to_keep_the_garment():
    text = build_prompt(NAVY).text
    assert "plain medium gray studio background" in text  # render_v1 배경 #808080
    assert "Keep the garment color, shape, length and fit exactly as in the render." in text
    assert not text.startswith("3DREAL.")  # 트리거는 with_trigger가 붙인다


def test_subject_defaults_to_a_person_and_can_be_the_mannequin():
    assert build_prompt(NAVY).text.startswith("A realistic photo of a person wearing a plain navy")
    mannequin = build_prompt(NAVY, subject="mannequin")
    assert mannequin.subject == "mannequin"
    assert SUBJECTS["mannequin"] in mannequin.text
    with pytest.raises(ValueError, match="모르는 대상"):
        build_prompt(NAVY, subject="robot")


def test_garment_text_overrides_the_description():
    prompt = build_prompt(NAVY, garment_text="  plain white linen T-shirt. ")
    assert prompt.garment == "plain white linen T-shirt"
    assert "navy" not in prompt.text
    with pytest.raises(ValueError, match="비었습니다"):
        build_prompt(NAVY, garment_text="  ")


def test_prompt_carries_its_version_and_sends_no_negative_prompt():
    prompt = build_prompt(NAVY)
    assert prompt.version == PROMPT_VERSION
    assert prompt.negative_prompt is None  # fal 기본 negative_prompt를 덮어쓰지 않는다
    assert prompt.to_dict()["text"] == prompt.text

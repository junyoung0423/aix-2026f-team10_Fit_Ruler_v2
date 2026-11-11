"""견본 검색 (#5): 기준 사례 7개, 동률 규칙, 일치 허용 오차, 오류 처리, 규칙 파일.

규칙은 팀 문서 `docs/decisions.md`의 "1주차 기준 [제안]" 4절이다. 가중치 가슴 0.4, 어깨 0.3,
총장 0.2, 소매 0.1(합 1.0, 총장이 없으면 0.8). 허리는 비교하지 않는다.
"""

import copy
import json
import random
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from fitting.catalog.search import (
    DEFAULT_SEARCH_RULES_PATH,
    ExcludeReason,
    SampleCandidate,
    SampleSearchError,
    SampleSearchErrorCode,
    SampleSearchResult,
    SearchRulesError,
    SkipReason,
    default_search_rules,
    load_search_rules,
    parse_search_rules,
    search_sample,
)
from fitting.domain import SCHEMA_VERSION, GarmentMeasurements, GarmentVariant

from ..conftest import REPO_ROOT

SAMPLES_PATH = REPO_ROOT / "tests" / "fixtures" / "catalog" / "samples_v0.json"
GARMENT_PATH = REPO_ROOT / "data" / "garments" / "tee_regular_m_navy_v1.json"


def _catalog() -> list[SampleCandidate]:
    raw = json.loads(SAMPLES_PATH.read_text(encoding="utf-8"))
    return [SampleCandidate.model_validate(item) for item in raw["samples"]]


def _demo_garment() -> GarmentMeasurements:
    """기준 티셔츠 1: 가슴 108, 어깨 46, 소매 22, 총장 70 (허리 96은 비교하지 않는다)."""
    demo = GarmentVariant.model_validate_json(GARMENT_PATH.read_text(encoding="utf-8"))
    return demo.measurements


def _garment(
    chest: float | None,
    shoulder: float | None,
    sleeve: float | None = None,
    length: float | None = None,
    waist: float | None = None,
) -> GarmentMeasurements:
    return GarmentMeasurements(
        chest_girth_cm=chest,
        shoulder_width_cm=shoulder,
        sleeve_length_cm=sleeve,
        body_length_cm=length,
        waist_girth_cm=waist,
    )


def _sample(sample_id: str, **cm: float | None) -> SampleCandidate:
    """치수 이름을 줄여 쓴다: chest, shoulder, sleeve, length. 안 쓴 치수는 null이다."""
    names = {
        "chest": "chest_girth_cm",
        "shoulder": "shoulder_width_cm",
        "sleeve": "sleeve_length_cm",
        "length": "body_length_cm",
    }
    return SampleCandidate(
        sample_id=sample_id,
        measurements=GarmentMeasurements(**{names[key]: value for key, value in cm.items()}),
    )


def _full(sample_id: str, chest: float, shoulder: float, sleeve: float, length: float):
    """네 치수가 모두 있는 견본."""
    return _sample(sample_id, chest=chest, shoulder=shoulder, sleeve=sleeve, length=length)


def _ranking(result: SampleSearchResult) -> list[tuple[str, float]]:
    return [(item.sample_id, item.score_cm) for item in result.ranking]


# --- 기준 사례 (지시서 4절) ---------------------------------------------------------------------


def test_case1_demo_garment_picks_the_closest_length():
    result = search_sample(_demo_garment(), _catalog())

    assert result.selected_sample_id == "tee_gc_regular_04"
    assert result.score_cm == 3.85
    assert result.exact_match is False  # 어깨가 11.5 cm 다르다
    assert result.tie_broken is False
    assert _ranking(result)[:3] == [
        ("tee_gc_regular_04", 3.85),
        ("tee_gc_regular_03", 4.25),
        ("tee_gc_regular_06", 5.45),
    ]
    assert result.selected_measurements == _catalog()[3].measurements
    assert result.excluded == []


def test_case1_third_place_is_a_tie_broken_by_id():
    """tee_gc_regular_06과 tee_gc_slim_02는 5.45 동률이고 가슴 차이도 4로 같다 → ID 순."""
    result = search_sample(_demo_garment(), _catalog())

    third, fourth = result.ranking[2], result.ranking[3]
    assert (third.sample_id, third.score_cm) == ("tee_gc_regular_06", 5.45)
    assert (fourth.sample_id, fourth.score_cm) == ("tee_gc_slim_02", 5.45)
    assert [c.diff_cm for c in third.compared if c.dimension == "chest_girth_cm"] == [4.0]
    assert [c.diff_cm for c in fourth.compared if c.dimension == "chest_girth_cm"] == [-4.0]


def test_case1_waist_is_not_compared_and_not_reported():
    """의류에 허리 96이 있어도 허리는 비교하지 않고, skipped에도 나오지 않는다."""
    result = search_sample(_demo_garment(), _catalog())

    assert _demo_garment().waist_girth_cm == 96.0
    for item in result.ranking:
        assert "waist_girth_cm" not in {c.dimension for c in item.compared}
        assert item.skipped == []


def test_case2_relaxed_garment():
    result = search_sample(_garment(118, 52, 24, 74), _catalog())

    assert result.selected_sample_id == "tee_gc_relaxed_08"
    assert result.score_cm == 5.85
    assert result.exact_match is False
    assert _ranking(result)[:3] == [
        ("tee_gc_relaxed_08", 5.85),
        ("tee_gc_relaxed_07", 7.05),
        ("tee_gc_regular_06", 8.25),
    ]


def test_case3_tie_is_broken_by_sample_id():
    result = search_sample(_garment(110, 34.5, 22, 72), _catalog())

    # regular_04(108)와 regular_06(112)는 점수 0.80, 가슴 차이 2로 같다 → ID 사전순
    assert _ranking(result)[:2] == [("tee_gc_regular_04", 0.8), ("tee_gc_regular_06", 0.8)]
    assert result.selected_sample_id == "tee_gc_regular_04"
    assert result.tie_broken is True


def test_case4_garment_without_length_divides_by_the_remaining_weights():
    """AI Hub 의류 모양: 총장이 없다. 가중치 합은 0.8이고 5.15 ÷ 0.8 = 6.4375 → 6.44."""
    result = search_sample(_garment(98, 43, 20), _catalog())

    assert result.selected_sample_id == "tee_gc_slim_01"
    assert result.score_cm == 6.44
    assert result.tie_broken is True  # slim_02와 같은 점수 → ID 순
    assert _ranking(result)[:3] == [
        ("tee_gc_slim_01", 6.44),
        ("tee_gc_slim_02", 6.44),
        ("tee_gc_regular_03", 8.44),
    ]
    best = result.ranking[0]
    assert [(c.dimension, c.weight) for c in best.compared] == [
        ("chest_girth_cm", 0.4),
        ("shoulder_width_cm", 0.3),
        ("sleeve_length_cm", 0.1),
    ]
    assert [(s.dimension, s.reason) for s in best.skipped] == [
        ("body_length_cm", SkipReason.GARMENT_VALUE_MISSING)
    ]


def test_case5_differences_within_half_a_cm_are_an_exact_match():
    result = search_sample(_garment(108.4, 34.5, 22.3, 71.6), _catalog())

    assert result.selected_sample_id == "tee_gc_regular_04"
    assert result.score_cm == 0.27
    assert result.exact_match is True  # 가장 큰 차이가 0.4 cm
    assert result.tie_broken is False


def test_case6_garment_without_shoulder_is_rejected():
    garment = GarmentMeasurements(chest_girth_cm=108.0, sleeve_length_cm=22.0, body_length_cm=70.0)

    with pytest.raises(SampleSearchError) as raised:
        search_sample(garment, _catalog())

    assert raised.value.code == "GARMENT_REQUIRED_MISSING"
    assert raised.value.code is SampleSearchErrorCode.GARMENT_REQUIRED_MISSING
    assert raised.value.missing == ("shoulder_width_cm",)
    assert "shoulder_width_cm" in str(raised.value)


def test_case6_reports_every_missing_required_dimension():
    with pytest.raises(SampleSearchError) as raised:
        search_sample(_garment(None, None, 22, 70), _catalog())

    assert raised.value.missing == ("chest_girth_cm", "shoulder_width_cm")  # 설정의 치수 순서


def test_case7_sample_without_sleeve_is_excluded():
    broken = _sample("tee_gc_broken_99", chest=108, shoulder=34.5, length=70)  # 소매가 없다
    plain = search_sample(_demo_garment(), _catalog())

    result = search_sample(_demo_garment(), [*_catalog(), broken])

    assert result.selected_sample_id == plain.selected_sample_id == "tee_gc_regular_04"
    assert result.ranking == plain.ranking
    assert len(result.excluded) == 1
    excluded = result.excluded[0]
    assert excluded.sample_id == "tee_gc_broken_99"
    assert excluded.reason is ExcludeReason.SAMPLE_REQUIRED_MISSING
    assert excluded.missing == ["sleeve_length_cm"]
    assert "tee_gc_broken_99" not in {item.sample_id for item in result.ranking}


# --- 견본은 비교 치수가 모두 있어야 후보 ---------------------------------------------------------


@pytest.mark.parametrize(
    ("missing", "expected"),
    [
        ({"chest"}, ["chest_girth_cm"]),
        ({"shoulder"}, ["shoulder_width_cm"]),
        ({"length"}, ["body_length_cm"]),
        ({"sleeve"}, ["sleeve_length_cm"]),
        ({"length", "chest", "sleeve"}, ["chest_girth_cm", "body_length_cm", "sleeve_length_cm"]),
    ],
)
def test_sample_missing_any_comparison_dimension_is_excluded(missing, expected):
    values = {"chest": 108, "shoulder": 34.5, "sleeve": 22, "length": 72}
    partial = _sample("partial", **{k: (None if k in missing else v) for k, v in values.items()})

    result = search_sample(_demo_garment(), [*_catalog(), partial])

    assert [(e.sample_id, e.missing) for e in result.excluded] == [("partial", expected)]
    assert "partial" not in {item.sample_id for item in result.ranking}


def test_a_sample_with_a_waist_value_is_still_a_candidate():
    """허리는 비교하지 않으므로 견본에 허리가 있어도 없어도 같다."""
    with_waist = SampleCandidate(
        sample_id="tee_gc_with_waist",
        measurements=GarmentMeasurements(
            chest_girth_cm=108.0,
            shoulder_width_cm=34.5,
            sleeve_length_cm=22.0,
            body_length_cm=72.0,
            waist_girth_cm=90.0,
        ),
    )

    result = search_sample(_demo_garment(), [with_waist])

    assert result.excluded == []
    assert result.score_cm == 3.85


def test_excluded_samples_are_listed_in_id_order():
    samples = [_sample("b", chest=1), _sample("a", chest=1), _full("ok", 108, 34.5, 22, 72)]

    result = search_sample(_demo_garment(), samples)

    assert [e.sample_id for e in result.excluded] == ["a", "b"]


# --- 점수 ---------------------------------------------------------------------------------------


def test_score_is_the_weighted_mean_absolute_difference():
    # 가슴 +2(0.4), 어깨 −1.5(0.3), 소매 0(0.1), 총장 +4(0.2) → 0.8 + 0.45 + 0 + 0.8 = 2.05
    result = search_sample(_garment(106, 36, 22, 68), [_full("s", 108, 34.5, 22, 72)])

    assert result.score_cm == 2.05
    comparisons = {c.dimension: c for c in result.ranking[0].compared}
    assert comparisons["chest_girth_cm"].diff_cm == 2.0  # 견본 − 의류
    assert comparisons["shoulder_width_cm"].diff_cm == -1.5
    assert comparisons["body_length_cm"].diff_cm == 4.0


def test_garment_without_sleeve_and_length_uses_only_chest_and_shoulder_weights():
    # 가슴 +2(0.4) + 어깨 −1.5(0.3) = 0.8 + 0.45 = 1.25, 가중치 합 0.7 → 1.7857… → 1.79
    result = search_sample(_garment(110, 36), [_full("s", 112, 34.5, 22, 72)])

    assert result.score_cm == 1.79
    assert [(s.dimension, s.reason.value) for s in result.ranking[0].skipped] == [
        ("body_length_cm", "GARMENT_VALUE_MISSING"),
        ("sleeve_length_cm", "GARMENT_VALUE_MISSING"),
    ]


def test_result_records_each_comparison_in_config_order():
    result = search_sample(_garment(118, 52, 24, 74), _catalog())
    best = result.ranking[0]

    assert result.schema_version == SCHEMA_VERSION
    assert result.rules_version == "sample_search_v0"
    assert best.rank == 1
    assert [(c.dimension, c.weight) for c in best.compared] == [
        ("chest_girth_cm", 0.4),
        ("shoulder_width_cm", 0.3),
        ("body_length_cm", 0.2),
        ("sleeve_length_cm", 0.1),
    ]
    shoulder = best.compared[1]
    assert (shoulder.garment_cm, shoulder.sample_cm, shoulder.diff_cm) == (52.0, 34.5, -17.5)
    assert [item.rank for item in result.ranking] == list(range(1, 11))


def test_result_round_trips_through_json():
    result = search_sample(_garment(118, 52, 24, 74), _catalog())

    assert SampleSearchResult.model_validate_json(result.model_dump_json()) == result


def _chest_and_shoulder_rules():
    return parse_search_rules(
        {
            "version": "t",
            "score_decimals": 2,
            "exact_match_tolerance_cm": 0.5,
            "dimensions": [
                {"name": "chest_girth_cm", "weight": 1.0, "required": True},
                {"name": "shoulder_width_cm", "weight": 1.0, "required": True},
            ],
        }
    )


def test_score_uses_decimal_rounding_not_float_noise():
    """106.1 − 100.1 같은 값이 이진 오차 없이 정확히 6.0으로 계산된다."""
    result = search_sample(
        _garment(100.1, 40),
        [_sample("a", chest=106.1, shoulder=40)],
        _chest_and_shoulder_rules(),
    )

    assert result.ranking[0].compared[0].diff_cm == 6.0
    assert result.score_cm == 3.0


def test_score_rounds_half_up_to_two_decimals():
    # (0.01 + 0) ÷ 2 = 0.005 → 0.01 (반올림은 .5를 올린다)
    result = search_sample(
        _garment(100, 40),
        [_sample("a", chest=100.01, shoulder=40)],
        _chest_and_shoulder_rules(),
    )

    assert result.score_cm == 0.01


# --- 정확히 일치 (허용 오차 0.5 cm, 경계 포함) -------------------------------------------------


@pytest.mark.parametrize(
    ("delta", "exact"),
    [
        (0.0, True),
        (0.4, True),
        (0.5, True),
        (-0.5, True),
        (0.6, False),
        (-0.6, False),
        (3.0, False),
    ],
)
def test_exact_match_boundary_is_inclusive(delta, exact):
    # 소매만 delta만큼 다르다. 나머지 세 치수는 같다.
    sample = _full("s", 108, 34.5, 22 + delta, 72)

    result = search_sample(_garment(108, 34.5, 22, 72), [sample])

    assert result.exact_match is exact


def test_exact_match_looks_at_the_largest_difference():
    """작은 차이가 여러 개여도 하나라도 0.5를 넘으면 일치가 아니다."""
    near = _full("near", 108.5, 35.0, 22.5, 72.5)  # 모두 0.5
    far = _full("far", 108.5, 35.0, 22.5, 72.6)  # 총장만 0.6

    assert search_sample(_garment(108, 34.5, 22, 72), [near]).exact_match is True
    assert search_sample(_garment(108, 34.5, 22, 72), [far]).exact_match is False


def _default_rules_with_tolerance(tolerance: float):
    data = yaml.safe_load(DEFAULT_SEARCH_RULES_PATH.read_text(encoding="utf-8"))
    data["exact_match_tolerance_cm"] = tolerance
    return parse_search_rules(data)


def test_exact_match_does_not_change_which_sample_is_chosen():
    """고르는 견본은 점수 순서 그대로다. 허용 오차는 exact_match 표시만 바꾼다."""
    samples = [_full("a_far", 112, 34.5, 22, 72), _full("z_near", 108.3, 34.5, 22, 72)]
    garment = _garment(108, 34.5, 22, 72)

    strict = search_sample(garment, samples, _default_rules_with_tolerance(0.0))
    loose = search_sample(garment, samples, _default_rules_with_tolerance(0.5))

    assert strict.selected_sample_id == loose.selected_sample_id == "z_near"
    assert (strict.exact_match, loose.exact_match) == (False, True)


# --- 순서와 동률 --------------------------------------------------------------------------------


def test_input_order_does_not_change_the_result():
    expected = search_sample(_garment(110, 34.5, 22, 72), _catalog())
    samples = _catalog()
    for seed in range(5):
        random.Random(seed).shuffle(samples)
        assert search_sample(_garment(110, 34.5, 22, 72), samples) == expected


def test_same_input_gives_same_result():
    garment = _garment(110, 34.5, 22, 72)

    assert search_sample(garment, _catalog()) == search_sample(garment, _catalog())


def test_tie_rule_1_smaller_chest_difference_beats_sample_id():
    """둘 다 점수 1.20이다. tee_gc_a는 ID가 앞서지만 가슴 차이가 3이고, tee_gc_b는 1이다."""
    near_chest = _full("tee_gc_b", 108, 34.5, 22, 66)  # 가슴 −1, 총장 −4
    far_chest = _full("tee_gc_a", 112, 34.5, 22, 70)  # 가슴 +3

    for order in ([far_chest, near_chest], [near_chest, far_chest]):
        result = search_sample(_garment(109, 34.5, 22, 70), order)

        assert _ranking(result) == [("tee_gc_b", 1.2), ("tee_gc_a", 1.2)]
        assert result.selected_sample_id == "tee_gc_b"
        assert result.tie_broken is True


def test_tie_rule_2_sample_id_ascending():
    """가슴이 +2, −2로 반대 방향이라 점수도 가슴 차이도 같다 → ID 사전순."""
    plus = _full("b_plus", 102, 44, 20, 66)
    minus = _full("a_minus", 98, 44, 20, 66)

    for order in ([plus, minus], [minus, plus]):
        result = search_sample(_garment(100, 44, 20, 66), order)
        assert [item.sample_id for item in result.ranking] == ["a_minus", "b_plus"]


def test_a_smaller_score_always_beats_the_tie_rules():
    close = _full("z_close", 100, 44, 20, 66)
    far = _full("a_far", 100, 44, 20, 70)  # 총장이 4 다르다

    result = search_sample(_garment(100, 44, 20, 66), [far, close])

    assert [item.sample_id for item in result.ranking] == ["z_close", "a_far"]
    assert result.tie_broken is False


def test_two_exact_matches_are_ordered_by_sample_id():
    same = (108, 46, 22, 70)

    result = search_sample(_garment(*same), [_full("b_twin", *same), _full("a_twin", *same)])

    assert result.selected_sample_id == "a_twin"
    assert result.exact_match is True
    assert result.tie_broken is True
    assert result.score_cm == 0.0


def test_a_single_candidate_is_not_a_tie():
    result = search_sample(_garment(108, 46), [_full("only", 110, 46, 22, 70)])

    assert result.tie_broken is False
    assert [item.rank for item in result.ranking] == [1]


def test_sample_id_format_is_not_enforced():
    """견본 ID 형식(`tee_gc_{style}_{번호}`)은 C가 정한다. 검색은 비어 있지 않으면 받는다."""
    samples = [_full("아무 이름", 108, 46, 22, 70), _full("x", 108, 46, 22, 71)]

    result = search_sample(_garment(108, 46, 22, 70), samples)

    assert result.selected_sample_id == "아무 이름"


# --- 오류 ---------------------------------------------------------------------------------------


def test_empty_catalog_has_no_candidates():
    with pytest.raises(SampleSearchError) as raised:
        search_sample(_garment(108, 46), [])

    assert raised.value.code == "NO_CANDIDATES"


def test_all_samples_excluded_has_no_candidates():
    samples = [_sample("a", shoulder=46), _sample("b", chest=108)]

    with pytest.raises(SampleSearchError) as raised:
        search_sample(_garment(108, 46), samples)

    assert raised.value.code == "NO_CANDIDATES"


def test_duplicate_sample_ids_are_rejected():
    samples = [*_catalog(), _full("tee_gc_slim_02", 1, 1, 1, 1)]

    with pytest.raises(SampleSearchError) as raised:
        search_sample(_garment(108, 46), samples)

    assert raised.value.code == "DUPLICATE_SAMPLE_ID"
    assert raised.value.sample_ids == ("tee_gc_slim_02",)


def test_garment_error_comes_before_catalog_errors():
    with pytest.raises(SampleSearchError) as raised:
        search_sample(_garment(None, 46), [])

    assert raised.value.code == "GARMENT_REQUIRED_MISSING"


def test_error_code_is_a_plain_string_value():
    error = SampleSearchError("NO_CANDIDATES", "x")

    assert error.code == "NO_CANDIDATES"
    assert str(error) == "x"
    with pytest.raises(ValueError):
        SampleSearchError("NOT_A_CODE", "x")


def test_sample_id_must_not_be_empty():
    with pytest.raises(ValueError):
        SampleCandidate(sample_id="", measurements=GarmentMeasurements())


# --- 입력 보존 ----------------------------------------------------------------------------------


def test_inputs_are_not_modified():
    garment = _garment(118, 52, 24, 74)
    samples = _catalog()
    garment_before = garment.model_dump()
    samples_before = [s.model_dump() for s in samples]
    ids_before = [s.sample_id for s in samples]

    search_sample(garment, samples)

    assert garment.model_dump() == garment_before
    assert [s.model_dump() for s in samples] == samples_before
    assert [s.sample_id for s in samples] == ids_before  # 순서도 그대로


def test_result_does_not_share_state_between_calls():
    first = search_sample(_garment(118, 52, 24, 74), _catalog())
    first.ranking.clear()  # 결과 목록을 바꿔도

    second = search_sample(_garment(118, 52, 24, 74), _catalog())

    assert len(second.ranking) == 10  # 다음 결과에 영향이 없다


# --- 규칙 파일 ----------------------------------------------------------------------------------


def _rules_dict() -> dict:
    return yaml.safe_load(DEFAULT_SEARCH_RULES_PATH.read_text(encoding="utf-8"))


def test_default_rules_file_is_loaded():
    rules = load_search_rules()

    assert rules.version == "sample_search_v0"
    assert rules.score_decimals == 2
    assert rules.exact_match_tolerance == Decimal("0.5")
    assert [(d.name, float(d.weight), d.required) for d in rules.dimensions] == [
        ("chest_girth_cm", 0.4, True),
        ("shoulder_width_cm", 0.3, True),
        ("body_length_cm", 0.2, False),
        ("sleeve_length_cm", 0.1, False),
    ]
    assert sum(d.weight for d in rules.dimensions) == Decimal("1.0")
    assert default_search_rules() is default_search_rules()  # 한 번만 읽는다


def test_waist_is_not_a_comparison_dimension():
    assert "waist_girth_cm" not in {d.name for d in load_search_rules().dimensions}


def test_every_dimension_in_rules_is_a_garment_measurement():
    names = {d.name for d in load_search_rules().dimensions}

    assert names <= set(GarmentMeasurements.model_fields)


def test_rules_can_be_loaded_from_another_path(tmp_path):
    data = _rules_dict()
    data["version"] = "custom"
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")

    assert load_search_rules(path).version == "custom"
    assert load_search_rules(str(path)).version == "custom"


def _mutate(change) -> dict:
    data = copy.deepcopy(_rules_dict())
    change(data)
    return data


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.update(unknown_key=1), "모르는 키"),
        (lambda d: d["dimensions"][0].update(extra=1), "모르는 키"),
        (lambda d: d.pop("version"), "version"),
        (lambda d: d.update(version=""), "version"),
        (lambda d: d.pop("score_decimals"), "score_decimals"),
        (lambda d: d.update(score_decimals=True), "score_decimals"),
        (lambda d: d.update(score_decimals=-1), "score_decimals"),
        (lambda d: d.update(score_decimals=7), "score_decimals"),
        (lambda d: d.update(score_decimals=1.5), "score_decimals"),
        (lambda d: d.pop("exact_match_tolerance_cm"), "exact_match_tolerance_cm"),
        (lambda d: d.update(exact_match_tolerance_cm=-0.1), "0 이상"),
        (lambda d: d.update(exact_match_tolerance_cm=True), "숫자"),
        (lambda d: d.update(exact_match_tolerance_cm="0.5"), "숫자"),
        (lambda d: d.update(exact_match_tolerance_cm=float("nan")), "유한한"),
        (lambda d: d.update(exact_match_tolerance_cm=float("inf")), "유한한"),
        (lambda d: d.update(dimensions=[]), "dimensions"),
        (lambda d: d.update(dimensions="chest_girth_cm"), "dimensions"),
        (lambda d: d["dimensions"][0].update(weight=0), "0보다 커야"),
        (lambda d: d["dimensions"][0].update(weight=-1.0), "0보다 커야"),
        (lambda d: d["dimensions"][0].update(weight="2"), "숫자"),
        (lambda d: d["dimensions"][0].update(weight=True), "숫자"),
        (lambda d: d["dimensions"][0].update(weight=float("inf")), "유한한"),
        (lambda d: d["dimensions"][0].pop("weight"), "weight"),
        (lambda d: d["dimensions"][0].update(required="yes"), "required"),
        (lambda d: d["dimensions"][0].update(name="neck_cm"), "모르는 치수"),
        (lambda d: d["dimensions"][1].update(name="chest_girth_cm"), "두 번"),
        (lambda d: [x.update(required=False) for x in d["dimensions"]], "required가 true"),
    ],
)
def test_invalid_rules_are_rejected(change, message):
    with pytest.raises(SearchRulesError, match=message):
        parse_search_rules(_mutate(change))


def test_zero_tolerance_is_allowed():
    rules = parse_search_rules(_mutate(lambda d: d.update(exact_match_tolerance_cm=0)))

    assert rules.exact_match_tolerance == Decimal(0)


def test_rules_must_be_a_mapping():
    with pytest.raises(SearchRulesError, match="키-값"):
        parse_search_rules(["version"])
    with pytest.raises(SearchRulesError, match="키-값"):
        parse_search_rules(None)


def test_missing_or_broken_rules_file_is_rejected(tmp_path):
    with pytest.raises(SearchRulesError, match="읽을 수 없습니다"):
        load_search_rules(tmp_path / "nope.yaml")
    broken = tmp_path / "broken.yaml"
    broken.write_text("version: [unclosed", encoding="utf-8")
    with pytest.raises(SearchRulesError, match="YAML"):
        load_search_rules(broken)
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text(
        "version: x\nscore_decimals: 2\nexact_match_tolerance_cm: 0.5\ndimensions: []\n",
        encoding="utf-8",
    )
    with pytest.raises(SearchRulesError, match=r"invalid\.yaml.*dimensions"):
        load_search_rules(invalid)


def test_changed_weights_change_the_selection():
    """총장 가중치를 키우면 같은 의류에서 다른 견본이 뽑힌다."""
    garment = _garment(108, 34.5, 22, 76)
    default = search_sample(garment, _catalog())

    def length_heavy(data: dict) -> None:
        weights = {
            "chest_girth_cm": 0.1,
            "shoulder_width_cm": 0.1,
            "body_length_cm": 0.7,
            "sleeve_length_cm": 0.1,
        }
        for item in data["dimensions"]:
            item["weight"] = weights[item["name"]]

    changed = search_sample(garment, _catalog(), parse_search_rules(_mutate(length_heavy)))

    assert (default.selected_sample_id, default.score_cm) == ("tee_gc_regular_04", 0.8)
    assert (changed.selected_sample_id, changed.score_cm) == ("tee_gc_oversized_10", 1.8)
    assert changed.ranking[0].compared[2].weight == 0.7


def test_required_in_rules_is_about_the_garment():
    """`required`는 의류에 꼭 있어야 하는가다. 총장을 필수로 하면 총장 없는 의류는 거절된다."""

    def length_required(data: dict) -> None:
        for item in data["dimensions"]:
            if item["name"] == "body_length_cm":
                item["required"] = True

    rules = parse_search_rules(_mutate(length_required))

    with pytest.raises(SampleSearchError) as raised:
        search_sample(_garment(108, 46, 22), _catalog(), rules)
    assert raised.value.missing == ("body_length_cm",)


def test_samples_need_every_dimension_in_the_rules_even_if_not_required():
    """어깨를 선택 치수로 바꿔도 견본은 설정에 있는 치수가 모두 있어야 한다."""

    def shoulder_optional(data: dict) -> None:
        for item in data["dimensions"]:
            if item["name"] == "shoulder_width_cm":
                item["required"] = False

    rules = parse_search_rules(_mutate(shoulder_optional))
    no_shoulder = _sample("no_shoulder", chest=108, sleeve=22, length=72)
    complete = _full("ok", 108, 34.5, 22, 72)

    # 의류는 어깨 없이도 검색할 수 있고(선택 치수), 어깨가 없는 견본은 후보가 아니다.
    result = search_sample(_garment(108, None, 22, 72), [no_shoulder, complete], rules)

    assert [item.sample_id for item in result.ranking] == ["ok"]
    assert [(e.sample_id, e.missing) for e in result.excluded] == [
        ("no_shoulder", ["shoulder_width_cm"])
    ]
    assert [s.dimension for s in result.ranking[0].skipped] == ["shoulder_width_cm"]


def test_tie_breakers_tolerate_rules_without_chest():
    """가슴이 규칙에 없어도 순서가 흔들리지 않고 ID 순으로 끝난다."""
    rules = parse_search_rules(
        {
            "version": "t",
            "score_decimals": 2,
            "exact_match_tolerance_cm": 0.5,
            "dimensions": [{"name": "body_length_cm", "weight": 1.0, "required": True}],
        }
    )
    samples = [_sample("b", length=68), _sample("a", length=64)]

    result = search_sample(_garment(100, 44, length=66), samples, rules)

    assert [item.sample_id for item in result.ranking] == ["a", "b"]


def test_rules_path_default_points_to_repo_config():
    assert DEFAULT_SEARCH_RULES_PATH == Path(REPO_ROOT / "config" / "sample_search_v0.yaml")
    assert DEFAULT_SEARCH_RULES_PATH.is_file()

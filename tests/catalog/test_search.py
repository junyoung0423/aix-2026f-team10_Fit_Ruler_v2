"""견본 검색 (#5): 기준 사례 6개, 동률 규칙, 오류 처리, 규칙 파일."""

import copy
import json
import random
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
from fitting.domain import SCHEMA_VERSION, GarmentMeasurements

from ..conftest import REPO_ROOT

SAMPLES_PATH = REPO_ROOT / "tests" / "fixtures" / "catalog" / "samples_v0.json"
GARMENT_PATH = REPO_ROOT / "data" / "garments" / "tee_regular_m_navy_v1.json"


def _catalog() -> list[SampleCandidate]:
    raw = json.loads(SAMPLES_PATH.read_text(encoding="utf-8"))
    return [SampleCandidate.model_validate(item) for item in raw["samples"]]


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
    """치수 이름을 줄여 쓴다: chest, shoulder, sleeve, length."""
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


def _ranking(result: SampleSearchResult) -> list[tuple[str, float]]:
    return [(item.sample_id, item.score_cm) for item in result.ranking]


# --- 기준 사례 (지시서 5절) ---------------------------------------------------------------------


def test_case1_exact_match_with_demo_garment_file():
    from fitting.domain import GarmentVariant

    demo = GarmentVariant.model_validate_json(GARMENT_PATH.read_text(encoding="utf-8"))
    result = search_sample(demo.measurements, _catalog())

    assert result.selected_sample_id == "gc_tee_reg_l"
    assert result.score_cm == 0.0
    assert result.exact_match is True
    assert result.tie_broken is False
    assert _ranking(result)[:2] == [("gc_tee_reg_l", 0.0), ("gc_tee_over_s", 1.2)]
    # 견본에 허리가 없다 → 허리만 건너뛴다.
    skipped = [(s.dimension, s.reason) for s in result.ranking[0].skipped]
    assert skipped == [("waist_girth_cm", SkipReason.SAMPLE_VALUE_MISSING)]
    assert result.selected_measurements == _catalog()[2].measurements
    assert result.excluded == []


def test_case2_closest_sample_and_runners_up():
    result = search_sample(_garment(116, 50, 23, 71), _catalog())

    assert result.selected_sample_id == "gc_tee_over_m"
    assert result.score_cm == 0.6
    assert result.exact_match is False
    assert result.tie_broken is False
    assert _ranking(result)[:3] == [
        ("gc_tee_over_m", 0.6),
        ("gc_tee_reg_xl", 1.4),
        ("gc_tee_reg_xxl", 1.6),
    ]


def test_case3_tie_is_broken_by_sample_id():
    result = search_sample(_garment(112, 49, 23, 71), _catalog())

    # reg_xl과 over_m은 점수 1.00, 비교 치수 4개, 어깨 차이 1, 가슴 차이 2로 모두 같다 → ID 순
    assert _ranking(result)[:2] == [("gc_tee_over_m", 1.0), ("gc_tee_reg_xl", 1.0)]
    assert result.selected_sample_id == "gc_tee_over_m"
    assert result.tie_broken is True
    assert result.exact_match is False


def test_case4_missing_optional_dimensions_are_skipped():
    """AI Hub 의류 모양: 총장·허리가 없다. 비교 3개, 가중치 합 4."""
    result = search_sample(_garment(98, 43, 20), _catalog())

    assert result.selected_sample_id == "gc_tee_reg_s"
    assert result.score_cm == 1.0
    assert _ranking(result)[:2] == [("gc_tee_reg_s", 1.0), ("gc_tee_reg_m", 1.75)]
    best = result.ranking[0]
    assert [c.dimension for c in best.compared] == [
        "shoulder_width_cm",
        "chest_girth_cm",
        "sleeve_length_cm",
    ]
    assert [(s.dimension, s.reason) for s in best.skipped] == [
        ("body_length_cm", SkipReason.GARMENT_VALUE_MISSING),
        ("waist_girth_cm", SkipReason.GARMENT_VALUE_MISSING),
    ]


def test_case5_garment_without_shoulder_is_rejected():
    garment = GarmentMeasurements(chest_girth_cm=108.0, sleeve_length_cm=22.0, body_length_cm=70.0)

    with pytest.raises(SampleSearchError) as raised:
        search_sample(garment, _catalog())

    assert raised.value.code == "GARMENT_REQUIRED_MISSING"
    assert raised.value.code is SampleSearchErrorCode.GARMENT_REQUIRED_MISSING
    assert raised.value.missing == ("shoulder_width_cm",)
    assert "shoulder_width_cm" in str(raised.value)


def test_case5_reports_every_missing_required_dimension():
    with pytest.raises(SampleSearchError) as raised:
        search_sample(_garment(None, None, 22, 70), _catalog())

    assert raised.value.missing == ("shoulder_width_cm", "chest_girth_cm")


def test_case6_sample_without_required_dimension_is_excluded():
    from fitting.domain import GarmentVariant

    demo = GarmentVariant.model_validate_json(GARMENT_PATH.read_text(encoding="utf-8"))
    broken = _sample("gc_tee_broken", shoulder=46, sleeve=22, length=70)  # 가슴이 없다
    plain = search_sample(demo.measurements, _catalog())

    result = search_sample(demo.measurements, [*_catalog(), broken])

    assert result.selected_sample_id == plain.selected_sample_id
    assert result.ranking == plain.ranking
    assert len(result.excluded) == 1
    excluded = result.excluded[0]
    assert excluded.sample_id == "gc_tee_broken"
    assert excluded.reason is ExcludeReason.SAMPLE_REQUIRED_MISSING
    assert excluded.missing == ["chest_girth_cm"]
    assert "gc_tee_broken" not in {item.sample_id for item in result.ranking}


# --- 결과 모양 ----------------------------------------------------------------------------------


def test_result_records_each_comparison_in_config_order():
    result = search_sample(_garment(116, 50, 23, 71), _catalog())
    best = result.ranking[0]

    assert result.schema_version == SCHEMA_VERSION
    assert result.rules_version == "sample_search_v0"
    assert best.rank == 1
    assert [(c.dimension, c.weight) for c in best.compared] == [
        ("shoulder_width_cm", 2.0),
        ("chest_girth_cm", 1.0),
        ("body_length_cm", 1.0),
        ("sleeve_length_cm", 1.0),
    ]
    chest = best.compared[1]
    assert (chest.garment_cm, chest.sample_cm, chest.diff_cm) == (116.0, 114.0, -2.0)  # 견본 − 의류
    assert [item.rank for item in result.ranking] == list(range(1, 11))


def test_result_round_trips_through_json():
    result = search_sample(_garment(116, 50, 23, 71), _catalog())

    assert SampleSearchResult.model_validate_json(result.model_dump_json()) == result


def test_score_uses_decimal_rounding_not_float_noise():
    """106.1 − 100.1 같은 값이 이진 오차 없이 정확히 6.0으로 계산된다."""
    rules = parse_search_rules(
        {
            "version": "t",
            "score_decimals": 2,
            "dimensions": [
                {"name": "chest_girth_cm", "weight": 1.0, "required": True},
                {"name": "shoulder_width_cm", "weight": 1.0, "required": True},
            ],
        }
    )
    result = search_sample(_garment(100.1, 40), [_sample("a", chest=106.1, shoulder=40)], rules)

    assert result.ranking[0].compared[0].diff_cm == 6.0
    assert result.score_cm == 3.0


def test_score_rounds_half_up_to_two_decimals():
    rules = parse_search_rules(
        {
            "version": "t",
            "score_decimals": 2,
            "dimensions": [
                {"name": "chest_girth_cm", "weight": 1.0, "required": True},
                {"name": "shoulder_width_cm", "weight": 1.0, "required": True},
            ],
        }
    )
    # (0.01 + 0)/2 = 0.005 → 0.01 (반올림은 .5를 올린다)
    result = search_sample(_garment(100, 40), [_sample("a", chest=100.01, shoulder=40)], rules)

    assert result.score_cm == 0.01
    assert result.exact_match is False


# --- 순서와 동률 --------------------------------------------------------------------------------


def test_input_order_does_not_change_the_result():
    expected = search_sample(_garment(112, 49, 23, 71), _catalog())
    samples = _catalog()
    for seed in range(5):
        random.Random(seed).shuffle(samples)
        assert search_sample(_garment(112, 49, 23, 71), samples) == expected


def test_same_input_gives_same_result():
    garment = _garment(112, 49, 23, 71)

    assert search_sample(garment, _catalog()) == search_sample(garment, _catalog())


GARMENT_FOR_TIES = _garment(100, 44, 20, 66)  # 가중치: 어깨 2, 가슴·소매·총장 1, 합 5


def test_tie_rule_1_more_compared_dimensions_wins():
    # 둘 다 점수 0.40. full은 4개 비교(가슴 +2 ÷ 5), partial은 3개 비교(가슴 +1.6 ÷ 4).
    # 규칙 ②③④는 모두 partial 쪽이 유리하다(어깨 차이 같음, 가슴 차이 작음, ID가 앞).
    full = _sample("z_full", chest=102, shoulder=44, sleeve=20, length=66)
    partial = _sample("a_partial", chest=101.6, shoulder=44, length=66)

    result = search_sample(GARMENT_FOR_TIES, [partial, full])

    assert _ranking(result) == [("z_full", 0.4), ("a_partial", 0.4)]
    assert len(result.ranking[0].compared) == 4
    assert len(result.ranking[1].compared) == 3
    assert result.tie_broken is True


def test_tie_rule_2_smaller_shoulder_difference_wins():
    # 둘 다 점수 0.80, 비교 4개. near_shoulder는 어깨 차이 0(가슴 +4), far_shoulder는 어깨 차이 1
    # (가슴 +2). 규칙 ③④는 far_shoulder 쪽이 유리하다(가슴 차이 작음, ID가 앞).
    near = _sample("z_near_shoulder", chest=104, shoulder=44, sleeve=20, length=66)
    far = _sample("a_far_shoulder", chest=102, shoulder=45, sleeve=20, length=66)

    result = search_sample(GARMENT_FOR_TIES, [far, near])

    assert _ranking(result) == [("z_near_shoulder", 0.8), ("a_far_shoulder", 0.8)]


def test_tie_rule_3_smaller_chest_difference_wins():
    # 어깨 차이는 0으로 같고 둘 다 점수 0.60. chest_close는 가슴 +1·총장 +2, chest_far는 가슴 +2·
    # 총장 +1. 규칙 ④는 chest_far 쪽이 유리하다(ID가 앞).
    close = _sample("z_chest_close", chest=101, shoulder=44, sleeve=20, length=68)
    far = _sample("a_chest_far", chest=102, shoulder=44, sleeve=20, length=67)

    result = search_sample(GARMENT_FOR_TIES, [far, close])

    assert _ranking(result) == [("z_chest_close", 0.6), ("a_chest_far", 0.6)]


def test_tie_rule_4_sample_id_ascending():
    # 가슴이 +2, −2로 반대 방향이라 모든 값이 같다.
    plus = _sample("b_plus", chest=102, shoulder=44, sleeve=20, length=66)
    minus = _sample("a_minus", chest=98, shoulder=44, sleeve=20, length=66)

    for order in ([plus, minus], [minus, plus]):
        result = search_sample(GARMENT_FOR_TIES, order)
        assert [item.sample_id for item in result.ranking] == ["a_minus", "b_plus"]


def test_two_exact_matches_are_ordered_by_sample_id():
    garment = _garment(108, 46, 22, 70)
    same = {"chest": 108, "shoulder": 46, "sleeve": 22, "length": 70}

    result = search_sample(garment, [_sample("b_twin", **same), _sample("a_twin", **same)])

    assert result.selected_sample_id == "a_twin"
    assert result.exact_match is True
    assert result.tie_broken is True
    assert result.score_cm == 0.0


def test_a_single_candidate_is_not_a_tie():
    result = search_sample(_garment(108, 46), [_sample("only", chest=110, shoulder=46)])

    assert result.tie_broken is False
    assert [item.rank for item in result.ranking] == [1]


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
    samples = [*_catalog(), _sample("gc_tee_reg_m", chest=1, shoulder=1)]

    with pytest.raises(SampleSearchError) as raised:
        search_sample(_garment(108, 46), samples)

    assert raised.value.code == "DUPLICATE_SAMPLE_ID"
    assert raised.value.sample_ids == ("gc_tee_reg_m",)


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
    garment = _garment(116, 50, 23, 71)
    samples = _catalog()
    garment_before = garment.model_dump()
    samples_before = [s.model_dump() for s in samples]
    ids_before = [s.sample_id for s in samples]

    search_sample(garment, samples)

    assert garment.model_dump() == garment_before
    assert [s.model_dump() for s in samples] == samples_before
    assert [s.sample_id for s in samples] == ids_before  # 순서도 그대로


def test_result_does_not_share_state_between_calls():
    first = search_sample(_garment(116, 50, 23, 71), _catalog())
    first.ranking.clear()  # 결과 목록을 바꿔도

    second = search_sample(_garment(116, 50, 23, 71), _catalog())

    assert len(second.ranking) == 10  # 다음 결과에 영향이 없다


# --- 규칙 파일 ----------------------------------------------------------------------------------


def _rules_dict() -> dict:
    return yaml.safe_load(DEFAULT_SEARCH_RULES_PATH.read_text(encoding="utf-8"))


def test_default_rules_file_is_loaded():
    rules = load_search_rules()

    assert rules.version == "sample_search_v0"
    assert rules.score_decimals == 2
    assert [(d.name, float(d.weight), d.required) for d in rules.dimensions] == [
        ("shoulder_width_cm", 2.0, True),
        ("chest_girth_cm", 1.0, True),
        ("body_length_cm", 1.0, False),
        ("sleeve_length_cm", 1.0, False),
        ("waist_girth_cm", 0.5, False),
    ]
    assert default_search_rules() is default_search_rules()  # 한 번만 읽는다


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
        (lambda d: d["dimensions"][1].update(name="shoulder_width_cm"), "두 번"),
        (lambda d: [x.update(required=False) for x in d["dimensions"]], "required가 true"),
    ],
)
def test_invalid_rules_are_rejected(change, message):
    with pytest.raises(SearchRulesError, match=message):
        parse_search_rules(_mutate(change))


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
    invalid.write_text("version: x\nscore_decimals: 2\ndimensions: []\n", encoding="utf-8")
    with pytest.raises(SearchRulesError, match=r"invalid\.yaml.*dimensions"):
        load_search_rules(invalid)


def test_changed_weights_change_the_selection():
    """가슴 가중치를 키우고 어깨 가중치를 줄이면 같은 의류에서 다른 견본이 뽑힌다."""
    garment = _garment(108, 50, 22, 70)
    default = search_sample(garment, _catalog())

    def chest_heavy(data: dict) -> None:
        for item in data["dimensions"]:
            if item["name"] == "shoulder_width_cm":
                item["weight"] = 0.1
            if item["name"] == "chest_girth_cm":
                item["weight"] = 3.0

    custom = parse_search_rules(_mutate(chest_heavy))
    changed = search_sample(garment, _catalog(), custom)

    assert default.selected_sample_id == "gc_tee_over_s"  # 어깨가 가까운 견본
    assert changed.selected_sample_id == "gc_tee_reg_l"  # 가슴이 같은 견본
    assert changed.ranking[0].compared[0].weight == 0.1


def test_required_dimension_set_comes_from_the_rules():
    """총장을 필수로 바꾸면 총장이 없는 의류는 거절되고, 총장이 없는 견본은 제외된다."""

    def length_required(data: dict) -> None:
        for item in data["dimensions"]:
            if item["name"] == "body_length_cm":
                item["required"] = True

    rules = parse_search_rules(_mutate(length_required))

    with pytest.raises(SampleSearchError) as raised:
        search_sample(_garment(108, 46, 22), _catalog(), rules)
    assert raised.value.missing == ("body_length_cm",)

    no_length = _sample("no_length", chest=108, shoulder=46, sleeve=22)
    result = search_sample(_garment(108, 46, 22, 70), [no_length, *_catalog()], rules)
    assert [e.sample_id for e in result.excluded] == ["no_length"]
    assert result.excluded[0].missing == ["body_length_cm"]


def test_tie_breakers_tolerate_rules_without_shoulder_or_chest():
    """어깨·가슴이 규칙에 없어도 순서가 흔들리지 않고 ID 순으로 끝난다."""
    rules = parse_search_rules(
        {
            "version": "t",
            "score_decimals": 2,
            "dimensions": [{"name": "body_length_cm", "weight": 1.0, "required": True}],
        }
    )
    samples = [_sample("b", length=68), _sample("a", length=64)]

    result = search_sample(_garment(100, 44, length=66), samples, rules)

    assert [item.sample_id for item in result.ranking] == ["a", "b"]


def test_rules_path_default_points_to_repo_config():
    assert DEFAULT_SEARCH_RULES_PATH == Path(REPO_ROOT / "config" / "sample_search_v0.yaml")
    assert DEFAULT_SEARCH_RULES_PATH.is_file()

"""핏 기준표를 설정 파일(config/fit_rules_v0.yaml)로 바꿀 수 있는지 확인한다 (#2)."""

import copy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from fitting.domain import FitLabel, Region
from fitting.domain.garment import FAMILY_BASIC_SHORT_SLEEVE_CREW
from fitting.fit import (
    DEFAULT_RULES_PATH,
    Band,
    FitRulesError,
    RegionRule,
    RuleFitCalculator,
    compute_report,
    default_fit_rules,
    load_fit_rules,
    parse_fit_rules,
)

from .conftest import REPO_ROOT

# --- 이전 저장소 rules.py(정민)에 코드로 있던 기준표. 기본 YAML이 이것과 같아야 한다 -------------

_STRETCH_NOTE = "신축성에 따라 실제 착용감은 달라질 수 있어 착용 가능 여부는 판단하지 않습니다."

OLD_CHEST = RegionRule(
    region=Region.CHEST,
    short_ko="가슴",
    noun_ko="가슴둘레",
    bands=(
        Band(Decimal("0"), False, FitLabel.INSUFFICIENT, "여유 부족 예상"),
        Band(Decimal("6"), False, FitLabel.SNUG, "밀착"),
        Band(Decimal("14"), True, FitLabel.REGULAR, "기본 여유"),
        Band(None, False, FitLabel.LOOSE, "넉넉"),
    ),
    negative_note=_STRETCH_NOTE,
)
OLD_WAIST = RegionRule(
    region=Region.WAIST,
    short_ko="허리",
    noun_ko="허리둘레",
    bands=(
        Band(Decimal("0"), False, FitLabel.INSUFFICIENT, "여유 부족 예상"),
        Band(Decimal("4"), False, FitLabel.SNUG, "밀착"),
        Band(Decimal("12"), True, FitLabel.REGULAR, "기본 여유"),
        Band(None, False, FitLabel.LOOSE, "넉넉"),
    ),
    negative_note=_STRETCH_NOTE,
)
OLD_SHOULDER = RegionRule(
    region=Region.SHOULDER,
    short_ko="어깨",
    noun_ko="어깨너비",
    bands=(
        Band(Decimal("-1"), False, FitLabel.NARROW, "좁음 예상"),
        Band(Decimal("2"), True, FitLabel.REGULAR, "기본 범위"),
        Band(None, False, FitLabel.DROPPED, "어깨선 내려옴 예상"),
    ),
    note="어깨 여유는 양쪽을 합한 값이며 한쪽 어깨당 값이 아닙니다.",
)


def _raw() -> dict:
    return yaml.safe_load(DEFAULT_RULES_PATH.read_text(encoding="utf-8"))


def _write(tmp_path: Path, data) -> Path:
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_default_path_is_the_repository_config():
    assert DEFAULT_RULES_PATH == REPO_ROOT / "config" / "fit_rules_v0.yaml"


def test_default_yaml_equals_the_previous_code_table():
    rules = load_fit_rules()
    assert rules.version == "fit_rules_v0"
    assert rules.applicable_families == frozenset({FAMILY_BASIC_SHORT_SLEEVE_CREW})
    assert rules.chest == OLD_CHEST
    assert rules.shoulder == OLD_SHOULDER
    assert rules.waist == OLD_WAIST
    assert default_fit_rules() == rules


def test_another_yaml_changes_the_judgement(tmp_path, body, make_garment):
    # 가슴 여유 8 cm는 기본 기준표에서 '기본 여유'(6 이상 14 이하)다.
    garment = make_garment(chest=108.0)
    before = compute_report(body, garment, job_id="j")
    assert before.regions[0].label is FitLabel.REGULAR

    data = _raw()
    data["version"] = "fit_rules_test"
    data["regions"]["chest"]["bands"][1]["upper"] = 10  # 밀착을 10 미만까지 넓힌다
    rules = load_fit_rules(_write(tmp_path, data))

    after = RuleFitCalculator(rules).calculate(
        body, garment, job_id="j", created_at=before.created_at
    )
    assert after.regions[0].label is FitLabel.SNUG
    assert after.rules_version == "fit_rules_test"
    assert after.summary.startswith("가슴 밀착")


def test_float_bounds_keep_the_written_digits(tmp_path, body, make_garment):
    """YAML의 6.1은 이진 오차 없이 10진수 6.1로 읽혀 경계 판정이 그대로 맞아야 한다."""
    data = _raw()
    data["regions"]["chest"]["bands"][1]["upper"] = 6.1
    rules = load_fit_rules(_write(tmp_path, data))
    assert rules.chest.bands[1].upper == Decimal("6.1")
    at = compute_report(body, make_garment(chest=106.1), job_id="j", rules=rules)
    assert at.regions[0].label is FitLabel.REGULAR  # 6.1은 '6.1 미만'이 아니다


def _mutations():
    def set_last_upper(d):
        d["regions"]["chest"]["bands"][-1]["upper"] = 30

    def middle_upper_null(d):
        d["regions"]["chest"]["bands"][1]["upper"] = None

    def not_ascending(d):
        d["regions"]["waist"]["bands"][2]["upper"] = 4

    def descending(d):
        d["regions"]["shoulder"]["bands"][0]["upper"] = 5

    def unknown_label(d):
        d["regions"]["chest"]["bands"][0]["label"] = "TIGHT"

    def non_band_label(d):
        d["regions"]["chest"]["bands"][0]["label"] = "UNKNOWN"

    def duplicated_label(d):
        d["regions"]["chest"]["bands"][1]["label"] = "INSUFFICIENT"

    def unknown_key(d):
        d["regions"]["chest"]["bands"][0]["uper"] = 1

    def unknown_region(d):
        d["regions"]["sleeve"] = d["regions"]["chest"]

    def missing_region(d):
        del d["regions"]["waist"]

    def missing_band_key(d):
        del d["regions"]["chest"]["bands"][0]["label_ko"]

    def missing_version(d):
        del d["version"]

    def string_upper(d):
        d["regions"]["chest"]["bands"][0]["upper"] = "0"

    def bool_upper(d):
        d["regions"]["chest"]["bands"][0]["upper"] = True

    def nan_upper(d):
        d["regions"]["chest"]["bands"][0]["upper"] = float("nan")

    def inclusive_not_bool(d):
        d["regions"]["chest"]["bands"][0]["upper_inclusive"] = "yes"

    def no_bands(d):
        d["regions"]["chest"]["bands"] = []

    def empty_version(d):
        d["version"] = ""

    def no_families(d):
        d["applicable_families"] = []

    def empty_label_ko(d):
        d["regions"]["chest"]["bands"][0]["label_ko"] = " "

    return {
        f.__name__: f
        for f in (
            set_last_upper,
            middle_upper_null,
            not_ascending,
            descending,
            unknown_label,
            non_band_label,
            duplicated_label,
            unknown_key,
            unknown_region,
            missing_region,
            missing_band_key,
            missing_version,
            string_upper,
            bool_upper,
            nan_upper,
            inclusive_not_bool,
            no_bands,
            empty_version,
            no_families,
            empty_label_ko,
        )
    }


MUTATIONS = _mutations()


@pytest.mark.parametrize("name", list(MUTATIONS))
def test_invalid_table_is_rejected_when_loaded(tmp_path, name):
    data = copy.deepcopy(_raw())
    MUTATIONS[name](data)
    with pytest.raises(FitRulesError):
        load_fit_rules(_write(tmp_path, data))


def test_error_message_points_to_the_problem():
    data = _raw()
    data["regions"]["chest"]["bands"][-1]["upper"] = 30
    with pytest.raises(FitRulesError, match=r"regions\.chest\.bands\[3\]\.upper"):
        parse_fit_rules(data)
    data = _raw()
    data["regions"]["chest"]["bands"][0]["label"] = "TIGHT"
    with pytest.raises(FitRulesError, match="모르는 라벨"):
        parse_fit_rules(data)


def test_missing_file_and_broken_yaml_are_rejected(tmp_path):
    with pytest.raises(FitRulesError, match="읽을 수 없습니다"):
        load_fit_rules(tmp_path / "nope.yaml")
    broken = tmp_path / "broken.yaml"
    broken.write_text("version: [unclosed", encoding="utf-8")
    with pytest.raises(FitRulesError, match="YAML"):
        load_fit_rules(broken)
    not_mapping = tmp_path / "list.yaml"
    not_mapping.write_text("- 1\n- 2\n", encoding="utf-8")
    with pytest.raises(FitRulesError):
        load_fit_rules(not_mapping)

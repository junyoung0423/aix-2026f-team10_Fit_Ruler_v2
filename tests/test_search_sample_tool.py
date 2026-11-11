"""견본 검색 명령어 (#5): python -m tools.search_sample."""

import json
import subprocess
import sys

import pytest

from fitting.catalog.search import DEFAULT_SEARCH_RULES_PATH, SampleSearchResult
from tools.search_sample import main

from .conftest import REPO_ROOT

GARMENT = REPO_ROOT / "data" / "garments" / "tee_regular_m_navy_v1.json"
SAMPLES = REPO_ROOT / "tests" / "fixtures" / "catalog" / "samples_v0.json"


def _run(capsys, *args: str) -> tuple[int, str, str]:
    code = main([str(a) for a in args])
    out, err = capsys.readouterr()
    return code, out, err


def test_garment_file_prints_a_valid_result(capsys):
    code, out, err = _run(capsys, "--garment", GARMENT, "--samples", SAMPLES)

    assert code == 0
    assert err == ""
    result = SampleSearchResult.model_validate_json(out)
    assert result.selected_sample_id == "gc_tee_reg_l"
    assert result.score_cm == 0.0
    assert result.exact_match is True
    assert len(result.ranking) == 10


def test_output_is_indented_utf8_json(capsys):
    _, out, _ = _run(capsys, "--garment", GARMENT, "--samples", SAMPLES)

    assert out.startswith('{\n  "schema_version"')
    assert json.loads(out)["rules_version"] == "sample_search_v0"


def test_top_limits_the_ranking_only(capsys):
    _, full, _ = _run(capsys, "--garment", GARMENT, "--samples", SAMPLES)
    code, out, _ = _run(capsys, "--garment", GARMENT, "--samples", SAMPLES, "--top", 3)

    assert code == 0
    result = SampleSearchResult.model_validate_json(out)
    assert [r.sample_id for r in result.ranking] == [
        "gc_tee_reg_l",
        "gc_tee_over_s",
        "gc_tee_reg_m",
    ]
    assert (
        result.selected_sample_id == SampleSearchResult.model_validate_json(full).selected_sample_id
    )


def test_dimensions_can_be_given_directly(capsys):
    code, out, _ = _run(
        capsys,
        *("--chest", 116, "--shoulder", 50, "--sleeve", 23, "--length", 71),
        *("--samples", SAMPLES, "--top", 3),
    )

    assert code == 0
    result = SampleSearchResult.model_validate_json(out)
    assert [(r.sample_id, r.score_cm) for r in result.ranking] == [
        ("gc_tee_over_m", 0.6),
        ("gc_tee_reg_xl", 1.4),
        ("gc_tee_reg_xxl", 1.6),
    ]


def test_waist_option_is_compared_when_the_sample_has_it(capsys, tmp_path):
    samples = json.loads(SAMPLES.read_text(encoding="utf-8"))
    samples["samples"][2]["measurements"]["waist_girth_cm"] = 90.0  # gc_tee_reg_l
    path = tmp_path / "samples.json"
    path.write_text(json.dumps(samples), encoding="utf-8")

    code, out, _ = _run(
        capsys,
        *("--chest", 108, "--shoulder", 46, "--sleeve", 22, "--length", 70, "--waist", 96),
        *("--samples", path),
    )

    best = SampleSearchResult.model_validate_json(out).ranking[0]
    assert code == 0
    assert best.sample_id == "gc_tee_reg_l"
    assert best.compared[-1].dimension == "waist_girth_cm"
    assert best.compared[-1].diff_cm == -6.0
    assert best.score_cm == 0.55  # 0.5 × 6 ÷ 5.5


def test_custom_rules_file_is_used(capsys, tmp_path):
    rules = DEFAULT_SEARCH_RULES_PATH.read_text(encoding="utf-8").replace(
        "version: sample_search_v0", "version: custom_v9"
    )
    path = tmp_path / "rules.yaml"
    path.write_text(rules, encoding="utf-8")

    code, out, _ = _run(capsys, "--garment", GARMENT, "--samples", SAMPLES, "--rules", path)

    assert code == 0
    assert json.loads(out)["rules_version"] == "custom_v9"


# --- 종료 코드 2: 견본을 고를 수 없음 -------------------------------------------------


def test_exit_2_when_required_garment_dimension_is_missing(capsys):
    code, out, err = _run(
        capsys, *("--chest", 108, "--sleeve", 22, "--length", 70), *("--samples", SAMPLES)
    )

    assert code == 2
    assert out == ""
    assert err.startswith("GARMENT_REQUIRED_MISSING: ")
    assert "shoulder_width_cm" in err
    assert err.count("\n") == 1  # 한 줄


def test_exit_2_when_catalog_is_empty(capsys, tmp_path):
    path = tmp_path / "empty.json"
    path.write_text('{"samples": []}', encoding="utf-8")

    code, out, err = _run(capsys, "--garment", GARMENT, "--samples", path)

    assert (code, out) == (2, "")
    assert err.startswith("NO_CANDIDATES: ")


def test_exit_2_when_sample_ids_repeat(capsys, tmp_path):
    samples = json.loads(SAMPLES.read_text(encoding="utf-8"))
    samples["samples"].append(samples["samples"][0])
    path = tmp_path / "dup.json"
    path.write_text(json.dumps(samples), encoding="utf-8")

    code, _, err = _run(capsys, "--garment", GARMENT, "--samples", path)

    assert code == 2
    assert err.startswith("DUPLICATE_SAMPLE_ID: ")
    assert "gc_tee_reg_s" in err


# --- 종료 코드 1: 파일·형식·사용법 오류 -----------------------------------------------


def test_exit_1_when_samples_file_is_missing(capsys, tmp_path):
    code, out, err = _run(capsys, "--garment", GARMENT, "--samples", tmp_path / "none.json")

    assert (code, out) == (1, "")
    assert "견본집 파일을 읽을 수 없습니다" in err


def test_exit_1_when_garment_file_is_missing(capsys, tmp_path):
    code, _, err = _run(capsys, "--garment", tmp_path / "none.json", "--samples", SAMPLES)

    assert code == 1
    assert "의류 파일을 읽을 수 없습니다" in err


@pytest.mark.parametrize("content", ["{not json", "[]", '{"samples": 3}', '{"note": "x"}'])
def test_exit_1_when_samples_file_has_the_wrong_shape(capsys, tmp_path, content):
    path = tmp_path / "samples.json"
    path.write_text(content, encoding="utf-8")

    code, out, err = _run(capsys, "--garment", GARMENT, "--samples", path)

    assert (code, out) == (1, "")
    assert err.startswith("오류: 견본집")


def test_exit_1_when_a_sample_is_malformed(capsys, tmp_path):
    path = tmp_path / "samples.json"
    path.write_text('{"samples": [{"sample_id": "", "measurements": {}}]}', encoding="utf-8")

    code, _, err = _run(capsys, "--garment", GARMENT, "--samples", path)

    assert code == 1
    assert "samples.0.sample_id" in err or "sample_id" in err


def test_exit_1_when_garment_file_is_not_a_garment(capsys, tmp_path):
    path = tmp_path / "garment.json"
    path.write_text('{"id": "x"}', encoding="utf-8")

    code, _, err = _run(capsys, "--garment", path, "--samples", SAMPLES)

    assert code == 1
    assert "의류 치수가 올바르지 않습니다" in err


@pytest.mark.parametrize("value", ["0", "-5", "300.5", "nan", "inf"])
def test_exit_1_when_a_direct_dimension_is_out_of_range(capsys, value):
    code, out, err = _run(capsys, *("--chest", value, "--shoulder", 46), *("--samples", SAMPLES))

    assert (code, out) == (1, "")
    assert "chest_girth_cm" in err


def test_exit_1_when_garment_file_and_dimensions_are_both_given(capsys):
    code, out, err = _run(capsys, "--garment", GARMENT, "--chest", 100, "--samples", SAMPLES)

    assert (code, out) == (1, "")
    assert "함께 쓸 수 없습니다" in err


def test_exit_1_when_no_garment_is_given(capsys):
    code, out, err = _run(capsys, "--samples", SAMPLES)

    assert (code, out) == (1, "")
    assert "--garment" in err


def test_exit_1_when_samples_option_is_missing(capsys):
    code, _, err = _run(capsys, "--garment", GARMENT)

    assert code == 1
    assert "--samples" in err


@pytest.mark.parametrize("top", ["0", "-1", "abc", "1.5"])
def test_exit_1_when_top_is_not_a_positive_integer(capsys, top):
    code, out, _ = _run(capsys, "--garment", GARMENT, "--samples", SAMPLES, "--top", top)

    assert (code, out) == (1, "")


def test_exit_1_when_a_dimension_is_not_a_number(capsys):
    code, _, _ = _run(capsys, "--chest", "abc", "--samples", SAMPLES)

    assert code == 1


def test_exit_1_when_rules_file_is_invalid(capsys, tmp_path):
    path = tmp_path / "rules.yaml"
    path.write_text("version: x\n", encoding="utf-8")

    code, out, err = _run(capsys, "--garment", GARMENT, "--samples", SAMPLES, "--rules", path)

    assert (code, out) == (1, "")
    assert "score_decimals" in err


def test_help_exits_zero(capsys):
    code, out, _ = _run(capsys, "--help")

    assert code == 0
    assert "--samples" in out


# --- 실제 명령어 ---------------------------------------------------------------------------------


def test_runs_as_a_module():
    done = subprocess.run(
        [sys.executable, "-m", "tools.search_sample", "--garment", str(GARMENT)]
        + ["--samples", str(SAMPLES), "--top", "1"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["selected_sample_id"] == "gc_tee_reg_l"


def test_module_exit_code_2_for_missing_required_dimension():
    done = subprocess.run(
        [sys.executable, "-m", "tools.search_sample", "--chest", "108", "--sleeve", "22"]
        + ["--length", "70", "--samples", str(SAMPLES)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert done.returncode == 2
    assert done.stdout == ""
    assert "GARMENT_REQUIRED_MISSING" in done.stderr

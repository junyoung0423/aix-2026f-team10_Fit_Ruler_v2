"""견본집 검사 명령 (#4). 커밋된 견본집과, 그것을 임시 폴더에 복사해 망가뜨린 경우."""

import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("numpy", reason="garment 선택 의존성 필요: pip install -e .[garment]")
pytest.importorskip("scipy", reason="garment 선택 의존성 필요: pip install -e .[garment]")
pytest.importorskip("svgpathtools", reason="garment 선택 의존성 필요: pip install -e .[garment]")

from tools.check_sample_catalog import (  # noqa: E402
    EXIT_OK,
    EXIT_PROBLEMS,
    EXIT_UNREADABLE,
    check_catalog,
    main,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG = REPO_ROOT / "data" / "samples" / "tee_gc_v1"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


@pytest.fixture
def copy_dir(tmp_path) -> Path:
    out = tmp_path / "tee_gc_v1"
    shutil.copytree(CATALOG, out)
    return out


def _first_id(root: Path) -> str:
    return _read(root / "catalog.json")["samples"][0]["sample_id"]


def _problems(root: Path) -> str:
    _, problems = check_catalog(root)
    assert problems, "검사가 실패해야 한다"
    return " | ".join(problems)


def test_committed_catalog_passes(capsys):
    n, problems = check_catalog(CATALOG)
    assert problems == []
    assert n >= 10
    assert main([str(CATALOG)]) == EXIT_OK
    assert "통과" in capsys.readouterr().out


def test_committed_catalog_is_search_format():
    """B3 견본집 형식과 같다: 항목은 sample_id·measurements뿐이고 허리는 null."""
    data = _read(CATALOG / "catalog.json")
    for item in data["samples"]:
        assert set(item) == {"sample_id", "measurements"}
        assert item["measurements"]["waist_girth_cm"] is None


def test_missing_file(copy_dir):
    sid = _first_id(copy_dir)
    (copy_dir / sid / "design.yaml").unlink()
    assert f"{sid}: 파일이 없습니다: design.yaml" in _problems(copy_dir)


def test_missing_sample_dir(copy_dir):
    sid = _first_id(copy_dir)
    shutil.rmtree(copy_dir / sid)
    assert f"{sid}: 파일이 없습니다" in _problems(copy_dir)


def test_hash_mismatch(copy_dir):
    sid = _first_id(copy_dir)
    path = copy_dir / sid / "sample.json"
    sample = _read(path)
    sample["pattern_sha256"] = "0" * 64
    _write(path, sample)
    assert f"{sid}: 패턴 해시가 기록과 다릅니다" in _problems(copy_dir)


def test_specification_changed(copy_dir):
    """도면을 바꾸면 해시와 치수가 기록과 달라진다."""
    sid = _first_id(copy_dir)
    path = copy_dir / sid / "specification.json"
    spec = _read(path)
    panel = spec["pattern"]["panels"]["left_ftorso"]
    panel["vertices"] = [[x * 1.05, y] for x, y in panel["vertices"]]
    _write(path, spec)
    text = _problems(copy_dir)
    assert f"{sid}: 패턴 해시가 기록과 다릅니다" in text
    assert f"{sid}: chest_girth_cm를 다시 재면" in text


def test_too_few_samples(copy_dir):
    data = _read(copy_dir / "catalog.json")
    for item in data["samples"][9:]:
        shutil.rmtree(copy_dir / item["sample_id"])
    data["samples"] = data["samples"][:9]
    _write(copy_dir / "catalog.json", data)
    assert "견본이 9개로 10개보다 적습니다" in _problems(copy_dir)
    assert main([str(copy_dir)]) == EXIT_PROBLEMS
    assert main([str(copy_dir), "--min-samples", "9"]) == EXIT_OK


def test_style_mismatch(copy_dir):
    sid = _first_id(copy_dir)  # slim 견본
    path = copy_dir / sid / "sample.json"
    sample = _read(path)
    sample["style"] = "regular"
    _write(path, sample)
    text = _problems(copy_dir)
    assert "style 구간은 slim인데 style이 regular" in text
    assert "ID의 style(slim)이 style 값(regular)과 다릅니다" in text


def test_catalog_and_sample_measurements_differ(copy_dir):
    data = _read(copy_dir / "catalog.json")
    data["samples"][0]["measurements"]["sleeve_length_cm"] += 0.5
    _write(copy_dir / "catalog.json", data)
    assert "catalog.json과 sample.json의 치수가 다릅니다" in _problems(copy_dir)


def test_extra_key_in_catalog_item(copy_dir):
    """SampleCandidate는 extra="forbid"라 버전 같은 정보는 맨 위 키로 둔다."""
    data = _read(copy_dir / "catalog.json")
    data["samples"][0]["style"] = "slim"
    _write(copy_dir / "catalog.json", data)
    assert "견본 검색 형식이 아닙니다" in _problems(copy_dir)


def test_residual_over_tolerance(copy_dir):
    sid = _first_id(copy_dir)
    path = copy_dir / sid / "sample.json"
    sample = _read(path)
    sample["targets_cm"]["body_length_cm"] -= 1.5
    sample["residuals_cm"]["body_length_cm"] += 1.5
    _write(path, sample)
    assert f"{sid}: body_length_cm 잔여 오차" in _problems(copy_dir)


def test_missing_measurement_and_unit_range(copy_dir):
    data = _read(copy_dir / "catalog.json")
    first, second = data["samples"][0], data["samples"][1]
    first["measurements"]["shoulder_width_cm"] = None
    # 범위(35~100 cm) 밖이지만 계약 상한(300 cm) 안: 검색 형식은 통과하고 단위 검사에서 걸린다
    second["measurements"]["body_length_cm"] = 250.0
    _write(copy_dir / "catalog.json", data)
    text = _problems(copy_dir)
    assert f"{first['sample_id']}: shoulder_width_cm 값이 없습니다" in text
    assert f"{second['sample_id']}: body_length_cm = " in text


def test_duplicate_id(copy_dir):
    data = _read(copy_dir / "catalog.json")
    data["samples"][1] = json.loads(json.dumps(data["samples"][0]))
    _write(copy_dir / "catalog.json", data)
    text = _problems(copy_dir)
    assert "겹치는 ID" in text
    assert "catalog.json에 없는 견본 폴더" in text


def test_unreadable_catalog(tmp_path):
    assert main([str(tmp_path / "nothing")]) == EXIT_UNREADABLE
    (tmp_path / "catalog.json").write_text("{", encoding="utf-8")
    assert main([str(tmp_path)]) == EXIT_UNREADABLE

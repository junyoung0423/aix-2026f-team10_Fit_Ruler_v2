"""등록 의류 점검 명령 (#3). 지어낸 의류로만 만든다."""

import json
from pathlib import Path

import pytest

from tools.check_garments import (
    EXIT_OK,
    EXIT_TOO_FEW_READY,
    EXIT_UNREADABLE,
    GARMENTS_DIR_ENV,
    check_garments,
    main,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _dump(directory: Path, name: str, data: dict) -> None:
    (directory / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _garment(make_garment, garment_id: str, **kwargs) -> dict:
    data = make_garment(**kwargs).model_dump(mode="json")
    data["id"] = garment_id
    return data


@pytest.fixture
def folder(tmp_path, make_garment) -> Path:
    """READY 2벌(정상), DRAFT 1벌(어깨 없음), 문제 의류 여러 벌."""
    _dump(tmp_path, "tee_ok_a.json", _garment(make_garment, "tee_ok_a"))
    _dump(tmp_path, "tee_ok_b.json", _garment(make_garment, "tee_ok_b", chest=98.0, shoulder=44.0))
    _dump(
        tmp_path,
        "tee_draft.json",
        _garment(make_garment, "tee_draft", shoulder=None, status="DRAFT"),
    )
    # READY인데 어깨가 없다 (모델 검증도 실패한다)
    ready_missing = _garment(make_garment, "tee_ready_missing")
    ready_missing["measurements"]["shoulder_width_cm"] = None
    _dump(tmp_path, "tee_ready_missing.json", ready_missing)
    # 단면 너비가 그대로 들어간 듯한 가슴(54.3 cm)
    _dump(tmp_path, "tee_flat.json", _garment(make_garment, "tee_flat", chest=54.3, waist=50.0))
    # mm로 들어간 듯한 소매(220.7)
    _dump(tmp_path, "tee_mm.json", _garment(make_garment, "tee_mm", sleeve=220.7))
    # 같은 id가 다른 파일에 있다
    _dump(tmp_path, "tee_copy.json", _garment(make_garment, "tee_ok_a"))
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("json이 아니라 건너뛴다", encoding="utf-8")
    return tmp_path


def test_summary(folder):
    report = check_garments(folder)
    assert report.files == 8
    assert dict(report.status_counts) == {"READY": 5, "DRAFT": 1}
    assert report.ready == 5
    invalid = dict(report.invalid)
    assert set(invalid) == {"broken.json", "tee_ready_missing.json"}
    assert report.ready_missing_required == [("tee_ready_missing.json", ["shoulder_width_cm"])]
    suspicious = {(f, field) for f, field, _ in report.suspicious}
    assert ("tee_flat.json", "chest_girth_cm") in suspicious
    assert ("tee_mm.json", "sleeve_length_cm") in suspicious
    assert not any(f.startswith("tee_ok") for f, _ in suspicious)
    assert report.duplicate_ids == {"tee_ok_a": ["tee_copy.json", "tee_ok_a.json"]}
    assert report.id_name_mismatch == ["tee_copy.json"]
    assert report.problem_files == {
        "broken.json",
        "tee_ready_missing.json",
        "tee_flat.json",
        "tee_mm.json",
        "tee_copy.json",
        "tee_ok_a.json",
    }


@pytest.mark.parametrize(
    ("chest", "shoulder", "flagged"),
    [
        (69.9, 30.0, True),  # 70 cm 미만
        (70.0, 30.0, False),  # 경계 70 cm는 통과
        (73.5, 46.0, True),  # 어깨의 1.6배(73.6) 미만
        (73.6, 46.0, False),  # 경계 1.6배는 통과
        (108.0, 46.0, False),  # 시연 의류
    ],
)
def test_flat_width_rule_boundaries(tmp_path, make_garment, chest, shoulder, flagged):
    _dump(tmp_path, "tee_x.json", _garment(make_garment, "tee_x", chest=chest, shoulder=shoulder))
    report = check_garments(tmp_path)
    hit = [r for f, field, r in report.suspicious if field == "chest_girth_cm"]
    assert bool(hit) is flagged


@pytest.mark.parametrize(
    ("sleeve", "flagged"), [(4.9, True), (5.0, False), (45.0, False), (45.1, True)]
)
def test_unit_range_boundaries(tmp_path, make_garment, sleeve, flagged):
    _dump(tmp_path, "tee_x.json", _garment(make_garment, "tee_x", sleeve=sleeve))
    report = check_garments(tmp_path)
    assert bool(report.suspicious) is flagged


def test_min_ready_exit_codes(folder, capsys):
    assert main([str(folder), "--min-ready", "5"]) == EXIT_OK
    assert main([str(folder), "--min-ready", "6"]) == EXIT_TOO_FEW_READY
    out = capsys.readouterr().out
    assert "문제 의류 6개" in out
    assert "ID 중복: 1" in out


def test_values_are_not_printed(folder, capsys):
    """요약에는 파일 이름·필드·규칙만 나오고 치수 값은 나오지 않는다."""
    main([str(folder)])
    out = capsys.readouterr().out
    assert "54.3" not in out
    assert "220.7" not in out


def test_default_directory_from_environment(folder, monkeypatch, capsys):
    monkeypatch.setenv(GARMENTS_DIR_ENV, str(folder))
    assert main([]) == EXIT_OK
    assert str(folder) in capsys.readouterr().out


def test_committed_garments_pass(monkeypatch):
    monkeypatch.delenv(GARMENTS_DIR_ENV, raising=False)
    report = check_garments(REPO_ROOT / "data" / "garments")
    assert report.problem_files == set()
    assert main(["--min-ready", "2"]) == EXIT_OK


def test_missing_directory(tmp_path):
    assert main([str(tmp_path / "nothing")]) == EXIT_UNREADABLE

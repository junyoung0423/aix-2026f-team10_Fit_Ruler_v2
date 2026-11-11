"""등록 명령 `python -m tools.register_aihub_garment` (#3, C1). 가짜 라벨만 쓴다."""

import json

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from fitting.domain import GarmentVariant
from server.app import create_app
from tools.register_aihub_garment import (
    DEFAULT_OUT,
    EXIT_EXISTS,
    EXIT_OK,
    EXIT_REJECTED,
    main,
    register,
)

from .aihub_fake import FAKE_PRODUCT_ID, fake_label
from .conftest import EXAMPLES, REPO_ROOT

GARMENT_ID = f"tee_unknown_unknown_white_aihub_{FAKE_PRODUCT_ID}"
SCHEMA = json.loads(
    (REPO_ROOT / "docs" / "api" / "schemas" / "garment_variant.schema.json").read_text(
        encoding="utf-8"
    )
)


@pytest.fixture
def label_file(tmp_path):
    def _write(**clothes):
        path = tmp_path / "labels" / "fake_label.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(fake_label(**clothes), ensure_ascii=False), encoding="utf-8")
        return path

    return _write


@pytest.fixture
def out_dir(tmp_path):
    return tmp_path / "garments"


def test_default_out_is_the_server_garment_directory():
    assert DEFAULT_OUT == REPO_ROOT / "data" / "garments"


def test_registers_one_file_named_by_id(label_file, out_dir, capsys):
    assert main([str(label_file()), "--out", str(out_dir)]) == EXIT_OK
    path = out_dir / f"{GARMENT_ID}.json"
    assert [p.name for p in out_dir.iterdir()] == [path.name]
    assert GARMENT_ID in capsys.readouterr().out

    garment = GarmentVariant.model_validate_json(path.read_bytes())
    assert garment.id == GARMENT_ID
    text = path.read_text(encoding="utf-8")
    assert text.endswith("}\n") and "\r\n" not in text


def test_registered_file_passes_the_schema(label_file, out_dir):
    path = register(label_file(), out_dir)
    Draft202012Validator(SCHEMA).validate(json.loads(path.read_text(encoding="utf-8")))


def test_registered_file_has_the_same_shape_as_the_demo_file(label_file, out_dir):
    path = register(label_file(), out_dir)
    demo = json.loads((EXAMPLES / "garment.json").read_text(encoding="utf-8"))
    registered = json.loads(path.read_text(encoding="utf-8"))
    assert list(registered) == list(demo)  # 키 순서까지 같다


def test_does_not_overwrite_an_existing_id(label_file, out_dir, capsys):
    assert main([str(label_file()), "--out", str(out_dir)]) == EXIT_OK
    path = out_dir / f"{GARMENT_ID}.json"
    path.write_text("손으로 고친 내용", encoding="utf-8")

    assert main([str(label_file(chest_size="120.0")), "--out", str(out_dir)]) == EXIT_EXISTS
    assert path.read_text(encoding="utf-8") == "손으로 고친 내용"
    assert "--force" in capsys.readouterr().err


def test_force_overwrites_an_existing_id(label_file, out_dir):
    assert main([str(label_file()), "--out", str(out_dir)]) == EXIT_OK
    assert main([str(label_file(chest_size="120.0")), "--out", str(out_dir), "--force"]) == EXIT_OK
    garment = GarmentVariant.model_validate_json((out_dir / f"{GARMENT_ID}.json").read_bytes())
    assert garment.measurements.chest_girth_cm == 120.0


def test_rejected_label_writes_nothing(label_file, out_dir, capsys):
    path = label_file(sleeve_length_type="long sleeves", chest_size="1080")
    assert main([str(path), "--out", str(out_dir)]) == EXIT_REJECTED
    assert not out_dir.exists()
    err = capsys.readouterr().err
    assert "반팔 라운드넥" in err and "mm" in err


def test_unreadable_label_is_reported(tmp_path, out_dir, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert main([str(bad), "--out", str(out_dir)]) == EXIT_REJECTED
    assert main([str(tmp_path / "missing.json"), "--out", str(out_dir)]) == EXIT_REJECTED
    assert not out_dir.exists()


def test_flat_width_flag_is_passed_through(label_file, out_dir):
    path = label_file(chest_size="55.0", waist_size="52.0")
    assert main([str(path), "--out", str(out_dir), "--chest-waist-flat-width"]) == EXIT_OK
    garment = GarmentVariant.model_validate_json((out_dir / f"{GARMENT_ID}.json").read_bytes())
    assert garment.measurements.chest_girth_cm == 110.0
    assert garment.measurement_meta.chest_from_flat_width is True


def test_server_lists_and_fits_a_registered_garment(label_file, out_dir):
    """등록한 의류가 핏 API에서 조회·계산된다 (docs/api/README.md "의류 등록 형식")."""
    register(label_file(), out_dir)
    draft_label = label_file(shoulder_width="null")
    draft = json.loads(draft_label.read_text(encoding="utf-8"))
    draft["dataset"]["dataset.name"] = "01_sou_900002_990002_wear_02top_02t-shirt_man"
    draft_label.write_text(json.dumps(draft), encoding="utf-8")
    register(draft_label, out_dir)

    client = TestClient(create_app(garments_dir=out_dir))
    items = client.get("/api/garments").json()["garments"]
    # DRAFT는 목록에서 빠진다
    assert [(i["garment"]["id"], i["is_demo"]) for i in items] == [(GARMENT_ID, False)]

    body = {"height_cm": 170.0, "chest_cm": 100.0, "waist_cm": 80.0, "shoulder_cm": 44.0,
            "arm_length_cm": 60.0}  # fmt: skip
    response = client.post(
        "/api/fit",
        json={
            "garment_variant_id": GARMENT_ID,
            "measurements": body,
            "measurement_protocol_version": "measure_v1",
        },
    )
    assert response.status_code == 200, response.text
    regions = {r["region"]: r for r in response.json()["regions"]}
    assert regions["chest"]["garment_cm"] == 110.0
    assert regions["waist"]["label"] == "UNKNOWN"  # 허리 위치 미확인
    assert regions["body_length"]["garment_cm"] is None

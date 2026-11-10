"""핏 API (#2): GET /api/garments, POST /api/fit, 오류 형식 ApiError."""

import json

import pytest
import yaml
from fastapi.testclient import TestClient

from fitting.domain import ApiError, FitReport, GarmentList
from fitting.fit import DEFAULT_RULES_PATH, parse_fit_rules
from server.app import DEFAULT_GARMENTS_DIR, GARMENTS_DIR_ENV, create_app

from .conftest import EXAMPLES

GARMENT_EXAMPLE = json.loads((EXAMPLES / "garment.json").read_text(encoding="utf-8"))
DEMO_ID = GARMENT_EXAMPLE["id"]

VALID_BODY = {
    "height_cm": 175.0,
    "chest_cm": 100.0,
    "waist_cm": 80.0,
    "shoulder_cm": 44.0,
    "arm_length_cm": 60.0,
}

# 신체 치수 지원 범위 (fitting.domain.measurements, 경계 포함)
RANGES = {
    "height_cm": (140.0, 210.0),
    "chest_cm": (60.0, 160.0),
    "waist_cm": (50.0, 160.0),
    "shoulder_cm": (28.0, 60.0),
    "arm_length_cm": (40.0, 85.0),
}


def _garment(garment_id: str, **changes) -> dict:
    data = json.loads(json.dumps(GARMENT_EXAMPLE))
    data["id"] = garment_id
    for key, value in changes.items():
        if key in ("is_measured", "measurement_protocol_version"):
            data["measurement_meta"][key] = value
        else:
            data[key] = value
    return data


def _write(directory, name: str, data) -> None:
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    (directory / name).write_text(text, encoding="utf-8")


@pytest.fixture
def garments_dir(tmp_path):
    _write(tmp_path, "a_demo.json", _garment(DEMO_ID))
    _write(tmp_path, "b_measured.json", _garment("tee_measured", is_measured=True))
    _write(tmp_path, "c_draft.json", _garment("tee_draft", status="DRAFT"))
    _write(tmp_path, "d_preparing.json", _garment("tee_preparing", status="PREPARING"))
    _write(tmp_path, "e_failed.json", _garment("tee_failed", status="FAILED"))
    _write(tmp_path, "f_family.json", _garment("tee_hoodie", garment_family="hoodie"))
    _write(
        tmp_path,
        "g_protocol.json",
        _garment("tee_old_protocol", measurement_protocol_version="measure_v0"),
    )
    _write(tmp_path, "h_broken.json", "{not json")
    _write(tmp_path, "i_invalid.json", {"id": "tee_invalid"})
    _write(tmp_path, "j_duplicate.json", _garment(DEMO_ID, color="red"))
    (tmp_path / "notes.txt").write_text("json이 아닌 파일은 읽지 않는다", encoding="utf-8")
    return tmp_path


@pytest.fixture
def client(garments_dir):
    return TestClient(create_app(garments_dir=garments_dir))


def _fit(client, garment_id=DEMO_ID, measurements=None, **extra):
    body = {
        "garment_variant_id": garment_id,
        "measurements": measurements if measurements is not None else dict(VALID_BODY),
        "measurement_protocol_version": "measure_v1",
        **extra,
    }
    return client.post("/api/fit", json=body)


def _error(response, status: int, code: str) -> ApiError:
    assert response.status_code == status, response.text
    data = response.json()
    assert "detail" not in data  # FastAPI 기본 오류 형식을 쓰지 않는다
    error = ApiError.model_validate(data)
    assert error.code.value == code
    return error


def _ids(client) -> list[str]:
    return [item["garment"]["id"] for item in client.get("/api/garments").json()["garments"]]


# --- GET /api/garments ---------------------------------------------------------------------


def test_garment_list_has_only_ready_garments(client):
    response = client.get("/api/garments")
    assert response.status_code == 200
    listing = GarmentList.model_validate(response.json())
    ids = [item.garment.id for item in listing.garments]
    # 준비 안 된 의류는 빠진다. 의류군·프로토콜이 다른 READY 의류는 목록에 있고 계산 때 거절된다.
    assert ids == [DEMO_ID, "tee_measured", "tee_hoodie", "tee_old_protocol"]
    assert all(item.garment.status.value == "READY" for item in listing.garments)


def test_garment_list_marks_demo_garments(client):
    items = {i["garment"]["id"]: i for i in client.get("/api/garments").json()["garments"]}
    assert items[DEMO_ID]["is_demo"] is True
    assert items["tee_measured"]["is_demo"] is False


def test_broken_and_duplicate_files_are_skipped(client):
    items = client.get("/api/garments").json()["garments"]
    demo = [i for i in items if i["garment"]["id"] == DEMO_ID]
    assert len(demo) == 1
    assert demo[0]["garment"]["color"] == "navy"  # 파일 이름 순서로 먼저 나온 파일


def test_empty_or_missing_directory_gives_empty_list(tmp_path):
    for directory in (tmp_path, tmp_path / "missing"):
        response = TestClient(create_app(garments_dir=directory)).get("/api/garments")
        assert response.status_code == 200
        assert response.json()["garments"] == []


def test_directory_from_environment(monkeypatch, garments_dir):
    monkeypatch.setenv(GARMENTS_DIR_ENV, str(garments_dir))
    assert "tee_measured" in _ids(TestClient(create_app()))


def test_default_directory_has_the_demo_garment():
    assert DEFAULT_GARMENTS_DIR.name == "garments"
    demo = json.loads((DEFAULT_GARMENTS_DIR / f"{DEMO_ID}.json").read_text(encoding="utf-8"))
    assert demo == GARMENT_EXAMPLE  # docs/api/examples/garment.json과 같은 시연용 의류
    client = TestClient(create_app(garments_dir=DEFAULT_GARMENTS_DIR))
    items = client.get("/api/garments").json()["garments"]
    assert [(i["garment"]["id"], i["is_demo"]) for i in items] == [(DEMO_ID, True)]


# --- POST /api/fit 정상 ----------------------------------------------------------------------


def test_fit_returns_the_example_report(client):
    response = _fit(client)
    assert response.status_code == 200, response.text
    report = FitReport.model_validate(response.json())
    assert report.job_id.startswith("fit_")
    got = response.json()
    example_text = (EXAMPLES / "fit_report.json").read_text(encoding="utf-8")
    example = FitReport.model_validate_json(example_text).model_dump(mode="json")
    for key in ("job_id", "created_at"):
        got.pop(key)
        example.pop(key)
    assert got == example


def test_fit_uses_the_given_rules(garments_dir):
    data = yaml.safe_load(DEFAULT_RULES_PATH.read_text(encoding="utf-8"))
    data["version"] = "fit_rules_test"
    data["regions"]["chest"]["bands"][1]["upper"] = 10
    app = create_app(garments_dir=garments_dir, rules=parse_fit_rules(data))
    report = _fit(TestClient(app)).json()
    assert report["rules_version"] == "fit_rules_test"
    assert report["regions"][0]["label"] == "SNUG"


@pytest.mark.parametrize("field", list(RANGES))
@pytest.mark.parametrize("edge", [0, 1])
def test_range_edges_are_accepted(client, field, edge):
    measurements = {**VALID_BODY, field: RANGES[field][edge]}
    assert _fit(client, measurements=measurements).status_code == 200


def test_integer_numbers_are_accepted(client):
    measurements = {k: int(v) for k, v in VALID_BODY.items()}
    assert _fit(client, measurements=measurements).status_code == 200


# --- POST /api/fit 입력 오류 (422 INPUT_INVALID) ------------------------------------------


@pytest.mark.parametrize("field", list(RANGES))
@pytest.mark.parametrize(("edge", "step"), [(0, -0.1), (1, 0.1)])
def test_values_just_outside_the_range_are_rejected(client, field, edge, step):
    low, high = RANGES[field]
    value = round(RANGES[field][edge] + step, 1)
    error = _error(_fit(client, measurements={**VALID_BODY, field: value}), 422, "INPUT_INVALID")
    assert len(error.fields) == 1
    item = error.fields[0]
    assert item.field == f"measurements.{field}"
    assert item.code.value == "OUT_OF_RANGE"
    assert (item.min, item.max) == (low, high)
    assert f"{low:g}~{high:g} cm" in item.message


@pytest.mark.parametrize("value", ["100", "100.0", True, None, [100], {"v": 100}])
def test_non_numbers_are_rejected(client, value):
    measurements = {**VALID_BODY, "chest_cm": value}
    error = _error(_fit(client, measurements=measurements), 422, "INPUT_INVALID")
    assert [(f.field, f.code.value) for f in error.fields] == [
        ("measurements.chest_cm", "NOT_A_NUMBER")
    ]


def test_unknown_fields_are_rejected(client):
    measurements = {**VALID_BODY, "hip_cm": 95.0}
    error = _error(_fit(client, measurements=measurements, mode="full"), 422, "INPUT_INVALID")
    assert sorted((f.field, f.code.value) for f in error.fields) == [
        ("measurements.hip_cm", "UNKNOWN_FIELD"),
        ("mode", "UNKNOWN_FIELD"),  # POST /api/fit에는 mode가 없다
    ]


def test_missing_fields_are_rejected(client):
    measurements = dict(VALID_BODY)
    del measurements["waist_cm"]
    error = _error(_fit(client, measurements=measurements), 422, "INPUT_INVALID")
    assert [(f.field, f.code.value) for f in error.fields] == [("measurements.waist_cm", "MISSING")]
    assert error.fields[0].message == "허리둘레 값을 입력해 주세요."


def test_unsupported_protocol_is_rejected(client):
    error = _error(_fit(client, measurement_protocol_version="measure_v9"), 422, "INPUT_INVALID")
    assert [(f.field, f.code.value) for f in error.fields] == [
        ("measurement_protocol_version", "UNSUPPORTED_VALUE")
    ]


def test_empty_garment_id_is_rejected(client):
    error = _error(_fit(client, garment_id=""), 422, "INPUT_INVALID")
    assert [(f.field, f.code.value) for f in error.fields] == [("garment_variant_id", "INVALID")]


@pytest.mark.parametrize("content", [b"{not json", b"", b"[]"])
def test_broken_body_is_rejected(client, content):
    response = client.post(
        "/api/fit", content=content, headers={"content-type": "application/json"}
    )
    error = _error(response, 422, "INPUT_INVALID")
    assert error.fields


def test_error_messages_do_not_leak_validator_text(client):
    text = _fit(client, measurements={**VALID_BODY, "chest_cm": "100"}).text
    assert "Input should be" not in text
    assert "pydantic" not in text


# --- POST /api/fit 의류 오류 ----------------------------------------------------------------


def test_unknown_garment_is_404(client):
    error = _error(_fit(client, garment_id="tee_nope"), 404, "GARMENT_NOT_FOUND")
    assert error.fields is None


@pytest.mark.parametrize(
    "garment_id",
    ["tee_draft", "tee_preparing", "tee_failed", "tee_hoodie", "tee_old_protocol"],
)
def test_garment_that_cannot_be_computed_is_not_ready(client, garment_id):
    response = _fit(client, garment_id=garment_id)
    error = _error(response, 409, "GARMENT_NOT_READY")
    assert error.safe_message == "선택한 의류를 아직 사용할 수 없어요. 다른 의류를 선택해 주세요."
    assert error.fields is None
    # 내부 사유(FitEngineError.reason)는 응답에 넣지 않는다
    for internal in ("DRAFT", "PREPARING", "FAILED", "hoodie", "measure_v0", "프로토콜", "의류군"):
        assert internal not in response.text

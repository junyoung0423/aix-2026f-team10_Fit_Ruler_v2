"""피팅 작업 API (#12): POST /api/fittings, GET /api/fittings/{id}, GET /api/artifacts/{id}.

작업이 끝나기를 기다릴 때는 `app.state.runner.wait`를 쓴다(`time.sleep`으로 맞추지 않는다).
"""

import json
import os
import struct
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from fitting.adapters.fake import FakeDraper, fake_ports
from fitting.domain import (
    ApiError,
    FitReport,
    FittingRequest,
    FittingStatus,
    GarmentVariant,
    JobStatus,
    Stage,
    StageStatus,
)
from fitting.fit import RuleFitCalculator
from fitting.services.fitting import run_fitting
from fitting.services.jobs import JobRunner
from server.app import (
    ARTIFACTS_DIR_ENV,
    FAKE_PLACEHOLDERS_ENV,
    FAKE_STAGE_DELAY_ENV,
    MAX_WORKERS_ENV,
    create_app,
)
from server.artifacts import is_valid_artifact_id
from server.placeholders import (
    PLACEHOLDER_HEIGHT,
    PLACEHOLDER_WIDTH,
    solid_png,
    with_placeholders,
)

from .conftest import EXAMPLES

WAIT = 5
GARMENT_EXAMPLE = json.loads((EXAMPLES / "garment.json").read_text(encoding="utf-8"))
REQUEST_EXAMPLE = json.loads((EXAMPLES / "fitting_request.json").read_text(encoding="utf-8"))
DEMO_ID = GARMENT_EXAMPLE["id"]
EXAMPLE_JOB_ID = "fit_sample_001"

FIT_BODY = {k: v for k, v in REQUEST_EXAMPLE.items() if k not in ("mode", "realize_consent")}


def _garment(garment_id: str, **changes) -> dict:
    data = json.loads(json.dumps(GARMENT_EXAMPLE))
    data["id"] = garment_id
    data.update(changes)
    return data


def _request(**changes) -> dict:
    body = json.loads(json.dumps(REQUEST_EXAMPLE))
    body.update(changes)
    return body


def _example(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


@pytest.fixture
def garments_dir(tmp_path):
    directory = tmp_path / "garments"
    directory.mkdir()
    for name, data in {
        "demo.json": _garment(DEMO_ID),
        "draft.json": _garment("tee_draft", status="DRAFT"),
        "hoodie.json": _garment("tee_hoodie", garment_family="hoodie"),
    }.items():
        (directory / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return directory


@pytest.fixture
def artifacts_dir(tmp_path):
    directory = tmp_path / "artifacts"
    directory.mkdir()
    return directory


@pytest.fixture
def client(garments_dir, artifacts_dir):
    app = create_app(garments_dir=garments_dir, artifacts_dir=artifacts_dir)
    with TestClient(app) as test_client:  # 끝날 때 lifespan이 실행기를 정리한다
        yield test_client


def _runner(client) -> JobRunner:
    return client.app.state.runner


def _post(client, body=None, **kwargs):
    return client.post("/api/fittings", json=_request() if body is None else body, **kwargs)


def _finish(client, job_id: str) -> FittingStatus:
    _runner(client).wait(job_id, WAIT)
    response = client.get(f"/api/fittings/{job_id}")
    assert response.status_code == 200
    return FittingStatus.model_validate(response.json())


def _error(response, status: int, code: str) -> ApiError:
    assert response.status_code == status, response.text
    data = response.json()
    assert "detail" not in data  # FastAPI 기본 오류 형식을 쓰지 않는다
    error = ApiError.model_validate(data)
    assert error.code.value == code
    return error


# --- POST /api/fittings -------------------------------------------------------------------------


def test_post_returns_202_with_the_report_and_a_job_id(client):
    response = _post(client)

    assert response.status_code == 202
    first = FittingStatus.model_validate(response.json())
    assert first.job_id.startswith("fit_")
    assert first.status is JobStatus.ACCEPTED
    assert first.report.status is StageStatus.SUCCEEDED
    assert len(first.report.regions) == 5
    assert first.visual.status is StageStatus.QUEUED
    assert {p.stage: p.status for p in first.stages}[Stage.BODY_FIT] is StageStatus.WAITING
    _finish(client, first.job_id)


def test_first_report_equals_the_fit_api_report(client):
    fit = client.post("/api/fit", json=FIT_BODY)
    first = FittingStatus.model_validate(_post(client).json())

    assert fit.status_code == 200
    expected = FitReport.model_validate(fit.json())
    assert first.report.regions == expected.regions
    assert first.report.summary == expected.summary
    assert first.report.warnings == expected.warnings
    assert first.report.rules_version == expected.rules_version
    _finish(client, first.job_id)


@pytest.mark.parametrize(
    ("changes", "example"),
    [
        ({}, "fitting_status_succeeded.json"),
        ({"realize_consent": False}, "fitting_status_no_consent.json"),
        ({"mode": "report_only"}, "fitting_status_report_only.json"),
    ],
)
def test_final_status_matches_the_contract_example(client, changes, example):
    first = _post(client, _request(**changes)).json()

    polled = client.get(f"/api/fittings/{first['job_id']}").json()
    final = _finish(client, first["job_id"]).model_dump(mode="json")

    # 작업 ID만 다르다. 산출물 ID는 `{job_id}_…`라서 함께 바뀐다.
    normalized = json.loads(json.dumps(final).replace(first["job_id"], EXAMPLE_JOB_ID))
    assert normalized == _example(example)
    assert polled["status"] in {"ACCEPTED", "RUNNING", "SUCCEEDED"}


def test_report_only_is_already_final_in_the_first_response(client):
    response = _post(client, _request(mode="report_only"))

    assert response.status_code == 202
    first = FittingStatus.model_validate(response.json())
    assert first.status is JobStatus.SUCCEEDED
    assert first.visual.status is StageStatus.SKIPPED
    assert first.visual.skip_reason.value == "REPORT_ONLY"
    assert len(first.report.regions) == 5
    assert client.get(f"/api/fittings/{first.job_id}").json() == response.json()


def test_no_consent_shows_the_skip_from_the_first_response(client):
    first = FittingStatus.model_validate(_post(client, _request(realize_consent=False)).json())

    by_stage = {p.stage: p.status for p in first.stages}
    assert by_stage[Stage.REALIZE] is StageStatus.SKIPPED
    assert by_stage[Stage.VALIDATE] is StageStatus.SKIPPED
    assert first.visual.skip_reason.value == "CONSENT_NOT_GIVEN"
    assert first.visual.quality_status is None
    _finish(client, first.job_id)


def test_consent_defaults_to_not_given(client):
    body = _request()
    del body["realize_consent"]

    first = FittingStatus.model_validate(_post(client, body).json())

    assert first.visual.skip_reason.value == "CONSENT_NOT_GIVEN"
    _finish(client, first.job_id)


# --- 오류 ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "field", "code"),
    [
        (
            lambda b: b["measurements"].update(chest_cm=300.0),
            "measurements.chest_cm",
            "OUT_OF_RANGE",
        ),
        (
            lambda b: b["measurements"].update(height_cm="175"),
            "measurements.height_cm",
            "NOT_A_NUMBER",
        ),
        (lambda b: b.update(unknown_field=1), "unknown_field", "UNKNOWN_FIELD"),
        (lambda b: b.pop("garment_variant_id"), "garment_variant_id", "MISSING"),
        (
            lambda b: b.update(measurement_protocol_version="measure_v0"),
            "measurement_protocol_version",
            "UNSUPPORTED_VALUE",
        ),
    ],
)
def test_invalid_input_is_422_with_fields(client, change, field, code):
    body = _request()
    change(body)

    error = _error(_post(client, body), 422, "INPUT_INVALID")

    assert [(f.field, f.code.value) for f in error.fields] == [(field, code)]
    assert len(_runner(client)) == 0  # 입력 오류로는 작업이 생기지 않는다


@pytest.mark.parametrize("body", [{"mode": "fast"}, {"realize_consent": "true"}, {"mode": None}])
def test_invalid_mode_or_consent_is_422(client, body):
    _error(_post(client, _request(**body)), 422, "INPUT_INVALID")


def test_body_that_is_not_json_is_422(client):
    response = client.post(
        "/api/fittings", content="{not json", headers={"Content-Type": "application/json"}
    )

    error = _error(response, 422, "INPUT_INVALID")
    assert error.fields[0].code.value == "INVALID"


def test_unknown_garment_is_404(client):
    error = _error(_post(client, _request(garment_variant_id="tee_nope")), 404, "GARMENT_NOT_FOUND")

    assert error.fields is None
    assert len(_runner(client)) == 0


@pytest.mark.parametrize("garment_id", ["tee_draft", "tee_hoodie"])
def test_garment_that_cannot_be_used_is_409(client, garment_id):
    _error(_post(client, _request(garment_variant_id=garment_id)), 409, "GARMENT_NOT_READY")

    assert len(_runner(client)) == 0


@pytest.mark.parametrize("job_id", ["fit_nope", "fit_000000000000", "x", "a%20b"])
def test_unknown_job_is_404(client, job_id):
    error = _error(client.get(f"/api/fittings/{job_id}"), 404, "JOB_NOT_FOUND")

    assert error.fields is None


def test_unknown_routes_keep_the_default_404(client):
    """화면은 경로가 없는 기본 404와 계약 오류를 구분한다(`isFittingApiMissing`). 바꾸지 않는다."""
    response = client.get("/api/nope")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


# --- Idempotency-Key ----------------------------------------------------------------------------


def test_same_key_and_body_returns_200_with_the_same_job(client):
    first = _post(client, headers={"Idempotency-Key": "abc-1"})
    again = _post(client, headers={"Idempotency-Key": "abc-1"})

    assert (first.status_code, again.status_code) == (202, 200)
    assert again.json()["job_id"] == first.json()["job_id"]
    assert again.json()["report"] == first.json()["report"]
    assert len(_runner(client)) == 1
    _finish(client, first.json()["job_id"])


def test_replay_returns_the_current_status(client):
    first = _post(client, headers={"Idempotency-Key": "k"}).json()
    _finish(client, first["job_id"])

    again = _post(client, headers={"Idempotency-Key": "k"})

    assert again.status_code == 200
    assert again.json()["status"] == "SUCCEEDED"


def test_same_key_with_a_different_body_is_422(client):
    first = _post(client, headers={"Idempotency-Key": "k"}).json()

    response = _post(client, _request(realize_consent=False), headers={"Idempotency-Key": "k"})

    error = _error(response, 422, "INPUT_INVALID")
    assert [(f.field, f.code.value) for f in error.fields] == [("Idempotency-Key", "INVALID")]
    assert len(_runner(client)) == 1
    _finish(client, first["job_id"])


def test_header_name_is_case_insensitive_and_blank_means_no_key(client):
    a = _post(client, headers={"idempotency-key": "k"})
    b = _post(client, headers={"IDEMPOTENCY-KEY": "k"})
    blank1 = _post(client, headers={"Idempotency-Key": "  "})
    blank2 = _post(client, headers={"Idempotency-Key": "  "})

    assert b.status_code == 200 and b.json()["job_id"] == a.json()["job_id"]
    assert blank1.status_code == blank2.status_code == 202
    assert blank1.json()["job_id"] != blank2.json()["job_id"]


def test_requests_without_a_key_always_create_jobs(client):
    ids = {_post(client).json()["job_id"] for _ in range(3)}

    assert len(ids) == 3


# --- 진행 중 상태 -------------------------------------------------------------------------------


@dataclass
class GatedDraper(FakeDraper):
    entered: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)

    def drape(self, request, garment):
        self.entered.set()
        assert self.release.wait(WAIT)
        return super().drape(request, garment)


def test_progress_and_queue_are_visible_over_http(garments_dir, artifacts_dir):
    gate = GatedDraper()
    ports = fake_ports(fit=RuleFitCalculator(), drape=gate)
    app = create_app(garments_dir=garments_dir, artifacts_dir=artifacts_dir, ports=ports)
    try:
        with TestClient(app) as client:
            first = _post(client).json()
            assert gate.entered.wait(WAIT)
            second = _post(client, _request(realize_consent=False)).json()

            running = client.get(f"/api/fittings/{first['job_id']}").json()
            waiting = client.get(f"/api/fittings/{second['job_id']}").json()

            assert (running["status"], running["visual"]["status"]) == ("RUNNING", "RUNNING")
            assert running["visual"]["stage"] == "DRAPE"
            assert running["report"]["regions"] == first["report"]["regions"]
            assert (waiting["status"], waiting["visual"]["status"]) == ("ACCEPTED", "QUEUED")
            assert {s["status"] for s in waiting["stages"][:2]} == {"SUCCEEDED"}  # 리포트는 끝났다

            gate.release.set()
            assert _finish(client, first["job_id"]).status is JobStatus.SUCCEEDED
            assert _finish(client, second["job_id"]).status is JobStatus.SUCCEEDED
    finally:
        gate.release.set()


# --- GET /api/artifacts/{id} --------------------------------------------------------------------


def _get_artifact(client, artifact_id: str):
    return client.get(f"/api/artifacts/{artifact_id}")


def test_existing_artifact_is_served_with_its_content_type(client, artifacts_dir):
    data = solid_png(2, 2, (1, 2, 3))
    (artifacts_dir / "abc_drape_preview.png").write_bytes(data)

    response = _get_artifact(client, "abc_drape_preview")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.content == data


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("a.PNG", "image/png"),
        ("a.jpg", "image/jpeg"),
        ("a.mp4", "video/mp4"),
        ("a.json", "application/json"),
        ("a.glb", "model/gltf-binary"),
        ("a.unknownext", "application/octet-stream"),
    ],
)
def test_content_type_follows_the_extension(client, artifacts_dir, filename, content_type):
    (artifacts_dir / filename).write_bytes(b"x")

    response = _get_artifact(client, "a")

    assert response.status_code == 200
    assert response.headers["content-type"].split(";")[0] == content_type


def test_first_file_by_name_wins_when_extensions_differ(client, artifacts_dir):
    (artifacts_dir / "dup.png").write_bytes(b"png")
    (artifacts_dir / "dup.json").write_bytes(b"{}")

    response = _get_artifact(client, "dup")

    assert response.headers["content-type"].startswith("application/json")  # dup.json < dup.png


@pytest.mark.parametrize(
    "artifact_id",
    [
        "nope",
        "..%2Fx",
        "%2e%2e%2fx",
        "a/b",
        "a%2Fb",
        "abc.png",  # 확장자는 ID에 넣지 않는다
        "a%20b",
        "x" * 129,
        "한글",
    ],
)
def test_unknown_or_malformed_artifact_id_is_404(client, artifacts_dir, artifact_id):
    (artifacts_dir / "abc.png").write_bytes(b"x")
    (artifacts_dir.parent / "x.png").write_bytes(b"outside")

    error = _error(_get_artifact(client, artifact_id), 404, "ARTIFACT_NOT_FOUND")

    assert error.fields is None


@pytest.mark.parametrize(
    ("artifact_id", "valid"),
    [
        ("abc", True),
        ("A-z_09", True),
        ("x" * 128, True),
        ("", False),
        ("x" * 129, False),
        ("abc\n", False),  # 끝의 줄바꿈도 거절한다
        ("a.b", False),
        ("a/b", False),
        ("..", False),
        ("a b", False),
        ("한글", False),
        ("a\x00b", False),
    ],
)
def test_artifact_id_format(artifact_id, valid):
    assert is_valid_artifact_id(artifact_id) is valid


def test_empty_artifact_id_is_404(client):
    _error(client.get("/api/artifacts/"), 404, "ARTIFACT_NOT_FOUND")


def test_longest_valid_artifact_id_is_served(client, artifacts_dir):
    (artifacts_dir / f"{'x' * 128}.png").write_bytes(b"x")

    assert _get_artifact(client, "x" * 128).status_code == 200


def test_symlink_that_leaves_the_folder_is_404(client, artifacts_dir, tmp_path):
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"secret")
    try:
        os.symlink(outside, artifacts_dir / "link.png")
    except (OSError, NotImplementedError):
        pytest.skip("이 환경에서는 심볼릭 링크를 만들 수 없다")

    _error(_get_artifact(client, "link"), 404, "ARTIFACT_NOT_FOUND")


def test_directory_with_the_artifact_name_is_404(client, artifacts_dir):
    (artifacts_dir / "d.png").mkdir()

    _error(_get_artifact(client, "d"), 404, "ARTIFACT_NOT_FOUND")


def test_missing_artifacts_folder_is_404(garments_dir, tmp_path):
    app = create_app(garments_dir=garments_dir, artifacts_dir=tmp_path / "no_such_folder")
    with TestClient(app) as client:
        _error(_get_artifact(client, "abc"), 404, "ARTIFACT_NOT_FOUND")


def test_artifacts_folder_comes_from_the_environment(garments_dir, tmp_path, monkeypatch):
    folder = tmp_path / "from_env"
    folder.mkdir()
    (folder / "e.png").write_bytes(b"x")
    monkeypatch.setenv(ARTIFACTS_DIR_ENV, str(folder))

    with TestClient(create_app(garments_dir=garments_dir)) as client:
        assert _get_artifact(client, "e").status_code == 200


def test_argument_beats_the_environment_for_the_artifacts_folder(
    garments_dir, artifacts_dir, tmp_path, monkeypatch
):
    monkeypatch.setenv(ARTIFACTS_DIR_ENV, str(tmp_path / "ignored"))
    (artifacts_dir / "e.png").write_bytes(b"x")

    with TestClient(create_app(garments_dir=garments_dir, artifacts_dir=artifacts_dir)) as client:
        assert _get_artifact(client, "e").status_code == 200


# --- 자리표시 PNG (FIT_FAKE_PLACEHOLDERS) -------------------------------------------------------


def _png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert data[12:16] == b"IHDR"
    return struct.unpack(">II", data[16:24])


def test_placeholder_png_is_a_valid_solid_png():
    data = solid_png(PLACEHOLDER_WIDTH, PLACEHOLDER_HEIGHT, (10, 20, 30))

    assert _png_size(data) == (384, 512)
    assert len(data) < 5000  # 단색이라 작다


def test_placeholders_serve_the_image_artifacts(garments_dir, artifacts_dir, monkeypatch):
    monkeypatch.setenv(FAKE_PLACEHOLDERS_ENV, "1")
    with TestClient(create_app(garments_dir=garments_dir, artifacts_dir=artifacts_dir)) as client:
        first = _post(client).json()
        final = _finish(client, first["job_id"])
        visual = final.visual

        images = {
            "3D 미리보기": visual.drape_preview_artifact_id,
            "대표 프레임": visual.frame_artifact_id,
            "대응 렌더 프레임": visual.render_frame_artifact_id,
        }
        bodies = {}
        for name, artifact_id in images.items():
            response = _get_artifact(client, artifact_id)
            assert response.status_code == 200, name
            assert response.headers["content-type"] == "image/png", name
            assert _png_size(response.content) == (384, 512), name
            bodies[name] = response.content

        assert len(set(bodies.values())) == 3  # 역할마다 색이 다르다
        # 영상은 만들지 않는다
        _error(_get_artifact(client, visual.render_artifact_id), 404, "ARTIFACT_NOT_FOUND")
        _error(_get_artifact(client, visual.video_artifact_id), 404, "ARTIFACT_NOT_FOUND")
        assert sorted(p.name for p in artifacts_dir.iterdir()) == sorted(
            f"{i}.png" for i in images.values()
        )  # 임시 파일이 남지 않는다


def test_placeholder_file_exists_when_its_id_first_appears(tmp_path):
    """상태에 ID가 처음 보일 때는 이미 파일을 열 수 있다."""
    ports = with_placeholders(fake_ports(), tmp_path)
    existed: list[bool] = []

    def on_progress(status: FittingStatus) -> None:
        for artifact_id in (
            status.visual.drape_preview_artifact_id,
            status.visual.frame_artifact_id,
            status.visual.render_frame_artifact_id,
        ):
            if artifact_id is not None:
                existed.append((tmp_path / f"{artifact_id}.png").is_file())

    run_fitting(
        FittingRequest.model_validate(REQUEST_EXAMPLE),
        GarmentVariant.model_validate(GARMENT_EXAMPLE),
        ports,
        job_id="fit_x",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        on_progress=on_progress,
    )

    assert existed and all(existed)


def test_no_placeholders_by_default(client, artifacts_dir):
    final = _finish(client, _post(client).json()["job_id"])

    assert final.visual.drape_preview_artifact_id is not None
    _error(_get_artifact(client, final.visual.drape_preview_artifact_id), 404, "ARTIFACT_NOT_FOUND")
    assert list(artifacts_dir.iterdir()) == []


@pytest.mark.parametrize("value", ["0", "true", "yes", ""])
def test_only_one_turns_placeholders_on(garments_dir, artifacts_dir, monkeypatch, value):
    monkeypatch.setenv(FAKE_PLACEHOLDERS_ENV, value)
    with TestClient(create_app(garments_dir=garments_dir, artifacts_dir=artifacts_dir)) as client:
        _finish(client, _post(client).json()["job_id"])

    assert list(artifacts_dir.iterdir()) == []


def test_unwritable_placeholder_folder_does_not_stop_the_job(garments_dir, tmp_path, monkeypatch):
    blocker = tmp_path / "blocker"
    blocker.write_text("파일이라 폴더를 만들 수 없다", encoding="utf-8")
    monkeypatch.setenv(FAKE_PLACEHOLDERS_ENV, "1")
    with TestClient(create_app(garments_dir=garments_dir, artifacts_dir=blocker / "sub")) as client:
        final = _finish(client, _post(client).json()["job_id"])

    assert final.status is JobStatus.SUCCEEDED


# --- 환경변수와 조립 ----------------------------------------------------------------------------


def test_stage_delay_and_workers_from_the_environment(garments_dir, artifacts_dir, monkeypatch):
    monkeypatch.setenv(FAKE_STAGE_DELAY_ENV, "0.001")
    monkeypatch.setenv(MAX_WORKERS_ENV, "2")
    with TestClient(create_app(garments_dir=garments_dir, artifacts_dir=artifacts_dir)) as client:
        final = _finish(client, _post(client).json()["job_id"])

    assert final.status is JobStatus.SUCCEEDED


@pytest.mark.parametrize(
    ("name", "value"),
    [
        (MAX_WORKERS_ENV, "0"),
        (MAX_WORKERS_ENV, "-1"),
        (MAX_WORKERS_ENV, "abc"),
        (MAX_WORKERS_ENV, "1.5"),
        (FAKE_STAGE_DELAY_ENV, "-1"),
        (FAKE_STAGE_DELAY_ENV, "abc"),
        (FAKE_STAGE_DELAY_ENV, "nan"),
        (FAKE_STAGE_DELAY_ENV, "inf"),
    ],
)
def test_bad_environment_values_fail_when_the_app_is_created(
    garments_dir, monkeypatch, name, value
):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=name):
        create_app(garments_dir=garments_dir)


def test_blank_environment_values_use_the_defaults(garments_dir, artifacts_dir, monkeypatch):
    monkeypatch.setenv(FAKE_STAGE_DELAY_ENV, "  ")
    monkeypatch.setenv(MAX_WORKERS_ENV, "")

    with TestClient(create_app(garments_dir=garments_dir, artifacts_dir=artifacts_dir)) as client:
        assert _finish(client, _post(client).json()["job_id"]).status is JobStatus.SUCCEEDED


def test_fake_environment_values_are_ignored_when_ports_are_given(garments_dir, monkeypatch):
    monkeypatch.setenv(FAKE_STAGE_DELAY_ENV, "abc")

    app = create_app(garments_dir=garments_dir, ports=fake_ports(fit=RuleFitCalculator()))

    assert app.state.runner is not None


def test_given_runner_is_used_and_shut_down_with_the_app(garments_dir, artifacts_dir):
    stopped = []

    class SpyRunner(JobRunner):
        def shutdown(self, **kwargs):
            stopped.append(True)
            super().shutdown(**kwargs)

    runner = SpyRunner(fake_ports(fit=RuleFitCalculator()))
    app = create_app(garments_dir=garments_dir, artifacts_dir=artifacts_dir, runner=runner)

    with TestClient(app) as client:
        assert client.app.state.runner is runner
        job_id = _post(client).json()["job_id"]
        assert runner.get(job_id) is not None
        runner.wait(job_id, WAIT)
        assert stopped == []

    assert stopped == [True]


def test_default_ports_use_the_real_fit_calculator(client):
    """가짜 계산기가 아니라 실제 핏 계산기를 쓴다: 판정 라벨이 있고 규칙 버전이 fit_rules_v0다."""
    first = FittingStatus.model_validate(_post(client).json())

    assert first.report.rules_version == "fit_rules_v0"
    assert first.report.regions[0].label.value != "UNKNOWN"
    _finish(client, first.job_id)


def test_existing_fit_and_garment_routes_are_unchanged(client):
    assert client.get("/api/garments").status_code == 200
    assert client.post("/api/fit", json=FIT_BODY).status_code == 200
    _error(
        client.post("/api/fit", json={**FIT_BODY, "garment_variant_id": "none"}),
        404,
        "GARMENT_NOT_FOUND",
    )


def test_openapi_lists_the_new_routes(client):
    paths = client.get("/openapi.json").json()["paths"]

    assert set(paths["/api/fittings"]["post"]["responses"]) >= {"200", "202", "404", "409", "422"}
    assert "404" in paths["/api/fittings/{job_id}"]["get"]["responses"]
    assert "/api/artifacts/{artifact_id}" in paths

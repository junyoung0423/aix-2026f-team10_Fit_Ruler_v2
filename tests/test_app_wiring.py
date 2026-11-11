"""서버가 어댑터 설정으로 조립되는지 (#12 #13). `TestClient`.

스텁 설정(`tests/stub_adapters.py`)으로 서버를 띄워 "실제 어댑터 경로"가 끝까지 이어지는지 본다:
작업 → 스텁이 산출물 폴더에 쓴 파일 → `GET /api/artifacts/{id}`가 그 파일을 준다.
"""

import json
import logging
import struct

import pytest
import yaml
from fastapi.testclient import TestClient

import server.app as app_module
from fitting.adapters.fake import fake_ports
from fitting.domain import FittingStatus, JobStatus, Stage, StageStatus
from fitting.fit import RuleFitCalculator
from server.app import (
    FAKE_PLACEHOLDERS_ENV,
    FAKE_STAGE_DELAY_ENV,
    create_app,
)
from server.wiring import PORT_NAMES, AdapterConfigError

from . import stub_adapters
from .conftest import EXAMPLES

WAIT = 5
STUB = "tests.stub_adapters"
ALL_STUBS = {
    "body": f"{STUB}:make_body",
    "drape": f"{STUB}:make_drape",
    "render": f"{STUB}:make_render",
    "realize": f"{STUB}:make_realize",
    "validate": f"{STUB}:make_validate",
}
ALL_FAKE = dict.fromkeys(PORT_NAMES, "fake")

GARMENT_EXAMPLE = json.loads((EXAMPLES / "garment.json").read_text(encoding="utf-8"))
REQUEST_EXAMPLE = json.loads((EXAMPLES / "fitting_request.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _reset_stubs():
    stub_adapters.reset()


@pytest.fixture
def garments_dir(tmp_path):
    directory = tmp_path / "garments"
    directory.mkdir()
    (directory / "demo.json").write_text(json.dumps(GARMENT_EXAMPLE), encoding="utf-8")
    return directory


@pytest.fixture
def artifacts_dir(tmp_path):
    return tmp_path / "artifacts"


def _config(tmp_path, ports: dict, name: str = "adapters.yaml"):
    path = tmp_path / name
    data = {"version": "adapters_v0", "ports": {**ALL_FAKE, **ports}}
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _app(garments_dir, artifacts_dir, adapters):
    return create_app(garments_dir=garments_dir, artifacts_dir=artifacts_dir, adapters=adapters)


def _post(client, **changes):
    return client.post("/api/fittings", json={**REQUEST_EXAMPLE, **changes})


def _finish(client, job_id: str) -> FittingStatus:
    client.app.state.runner.wait(job_id, WAIT)
    return FittingStatus.model_validate(client.get(f"/api/fittings/{job_id}").json())


def _png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


# --- 스텁(실제 어댑터 경로)으로 끝까지 ---


def test_stub_adapters_run_the_whole_flow_and_serve_their_files(
    tmp_path, garments_dir, artifacts_dir
):
    app = _app(garments_dir, artifacts_dir, _config(tmp_path, ALL_STUBS))
    with TestClient(app) as client:
        first = _post(client)
        assert first.status_code == 202
        job_id = first.json()["job_id"]
        final = _finish(client, job_id)

        assert final.status is JobStatus.SUCCEEDED
        assert final.visual.quality_status.value == "PASS"
        # 스텁이 `settings.artifacts_dir`에 쓴 실제 파일이 산출물 API로 나온다
        preview = client.get(f"/api/artifacts/{final.visual.drape_preview_artifact_id}")
        assert preview.status_code == 200
        assert preview.headers["content-type"] == "image/png"
        assert _png_size(preview.content) == (2, 2)  # 스텁의 2×2 PNG, 자리표시(384×512)가 아니다
        assert preview.content == (artifacts_dir / f"{job_id}_drape_preview.png").read_bytes()
        for artifact_id, content_type in (
            (final.visual.render_artifact_id, "video/mp4"),
            (final.visual.video_artifact_id, "video/mp4"),
            (final.visual.frame_artifact_id, "image/png"),
            (final.visual.render_frame_artifact_id, "image/png"),
        ):
            response = client.get(f"/api/artifacts/{artifact_id}")
            assert response.status_code == 200
            assert response.headers["content-type"] == content_type

    called = [port for port, _ in stub_adapters.CALLS]
    assert called == ["body", "drape", "render", "realize", "validate"]


def test_stub_artifacts_go_to_the_servers_artifacts_folder(tmp_path, garments_dir, artifacts_dir):
    app = _app(garments_dir, artifacts_dir, _config(tmp_path, ALL_STUBS))
    with TestClient(app) as client:
        job_id = _post(client).json()["job_id"]
        _finish(client, job_id)

    assert stub_adapters.SEEN_SETTINGS[0].artifacts_dir == artifacts_dir
    assert (artifacts_dir / f"{job_id}_video.mp4").is_file()


def test_failing_stub_drape_gives_a_partial_success_and_keeps_the_report(
    tmp_path, garments_dir, artifacts_dir
):
    adapters = _config(tmp_path, {**ALL_STUBS, "drape": f"{STUB}:make_failing_drape"})
    with TestClient(_app(garments_dir, artifacts_dir, adapters)) as client:
        first = _post(client).json()
        final = _finish(client, first["job_id"])

    assert final.status is JobStatus.PARTIAL_SUCCEEDED
    assert final.visual.status is StageStatus.FAILED
    assert final.visual.stage is Stage.DRAPE
    assert final.visual.error_code.value == "DRAPE_INVALID"
    assert final.visual.drape_preview_artifact_id is None
    assert final.report.regions == FittingStatus.model_validate(first).report.regions
    by_stage = {p.stage: p.status for p in final.stages}
    assert by_stage[Stage.BODY_FIT] is StageStatus.SUCCEEDED
    assert by_stage[Stage.RENDER] is StageStatus.SKIPPED
    assert [port for port, _ in stub_adapters.CALLS] == ["body", "drape"]  # 뒤 단계는 부르지 않는다


def test_a_stub_that_raises_unexpectedly_is_still_a_partial_success(
    tmp_path, garments_dir, artifacts_dir
):
    adapters = _config(tmp_path, {**ALL_STUBS, "render": f"{STUB}:make_exploding_render"})
    with TestClient(_app(garments_dir, artifacts_dir, adapters)) as client:
        final = _finish(client, _post(client).json()["job_id"])

    assert final.status is JobStatus.PARTIAL_SUCCEEDED
    assert final.visual.stage is Stage.RENDER
    assert "터졌다" not in final.model_dump_json()  # 예외 내용은 상태에 나가지 않는다


def test_real_and_fake_ports_can_be_mixed(tmp_path, garments_dir, artifacts_dir):
    adapters = _config(tmp_path, {"drape": ALL_STUBS["drape"]})
    with TestClient(_app(garments_dir, artifacts_dir, adapters)) as client:
        job_id = _post(client).json()["job_id"]
        final = _finish(client, job_id)

        assert final.status is JobStatus.SUCCEEDED
        # 착장만 실제(스텁)라 3D 미리보기 파일만 있다. 렌더·실사화·검증은 가짜라 파일이 없다.
        assert (
            client.get(f"/api/artifacts/{final.visual.drape_preview_artifact_id}").status_code
            == 200
        )
        assert client.get(f"/api/artifacts/{final.visual.render_artifact_id}").status_code == 404
        assert client.get(f"/api/artifacts/{final.visual.frame_artifact_id}").status_code == 404
    assert [port for port, _ in stub_adapters.CALLS] == ["drape"]


# --- 설정이 틀리면 서버가 뜨지 않는다 ---


@pytest.mark.parametrize(
    ("ports", "fragment"),
    [
        ({"drape": "no.such.module:make"}, "drape"),
        ({"render": f"{STUB}:no_such_factory"}, "render"),
        ({"realize": f"{STUB}:make_raises"}, "realize"),
        ({"validate": f"{STUB}:make_wrong_object"}, "validate"),
        ({"body": f"{STUB}:make_config_error"}, "Blender 경로가 없습니다"),
    ],
)
def test_wrong_config_stops_create_app_instead_of_falling_back_to_fake(
    tmp_path, garments_dir, artifacts_dir, ports, fragment
):
    with pytest.raises(AdapterConfigError) as raised:
        _app(garments_dir, artifacts_dir, _config(tmp_path, ports))

    assert fragment in str(raised.value)


def test_every_wrong_port_is_listed(tmp_path, garments_dir, artifacts_dir):
    adapters = _config(tmp_path, {"drape": "a.b:c", "validate": "d.e:f"})

    with pytest.raises(AdapterConfigError) as raised:
        _app(garments_dir, artifacts_dir, adapters)

    assert "a.b:c" in str(raised.value) and "d.e:f" in str(raised.value)


def test_a_malformed_config_file_stops_create_app(tmp_path, garments_dir, artifacts_dir):
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: adapters_v0\nports: {body: fake}\n", encoding="utf-8")

    with pytest.raises(AdapterConfigError, match="필요한 키가 없습니다"):
        _app(garments_dir, artifacts_dir, bad)


def test_a_missing_config_file_stops_create_app(tmp_path, garments_dir, artifacts_dir):
    with pytest.raises(AdapterConfigError, match="찾을 수 없습니다"):
        _app(garments_dir, artifacts_dir, tmp_path / "없는.yaml")


def test_the_environment_variable_picks_the_config(
    tmp_path, garments_dir, artifacts_dir, monkeypatch
):
    monkeypatch.setenv("FIT_ADAPTERS", str(_config(tmp_path, {"drape": "no.such.module:make"})))

    with pytest.raises(AdapterConfigError, match="drape"):
        create_app(garments_dir=garments_dir, artifacts_dir=artifacts_dir)


def test_the_argument_beats_the_environment_variable(
    tmp_path, garments_dir, artifacts_dir, monkeypatch
):
    monkeypatch.setenv("FIT_ADAPTERS", str(_config(tmp_path, {"drape": "no.such.module:make"})))

    app = create_app(garments_dir=garments_dir, artifacts_dir=artifacts_dir, adapters="fake")

    assert app.state.adapters == ALL_FAKE


def test_without_environment_the_default_config_is_used(garments_dir, artifacts_dir, monkeypatch):
    monkeypatch.delenv("FIT_ADAPTERS")

    app = create_app(garments_dir=garments_dir, artifacts_dir=artifacts_dir)

    assert app.state.adapters == app_module.describe(app_module.load_adapter_config("default"))


def test_given_ports_or_runner_skip_the_adapter_config(
    tmp_path, garments_dir, artifacts_dir, monkeypatch
):
    """`ports`·`runner`를 직접 넘기면 어댑터 설정을 읽지 않는다(틀려도 상관없다)."""
    monkeypatch.setenv("FIT_ADAPTERS", str(tmp_path / "없는.yaml"))

    with_ports = create_app(
        garments_dir=garments_dir,
        artifacts_dir=artifacts_dir,
        ports=fake_ports(fit=RuleFitCalculator()),
    )

    assert with_ports.state.adapters is None


# --- 가짜 전용 옵션은 전부 가짜일 때만 ---


def test_fake_only_options_are_ignored_when_any_port_is_real(
    tmp_path, garments_dir, artifacts_dir, monkeypatch, caplog
):
    monkeypatch.setenv(FAKE_PLACEHOLDERS_ENV, "1")
    monkeypatch.setenv(FAKE_STAGE_DELAY_ENV, "3600")  # 적용되면 작업이 한 시간 걸린다
    adapters = _config(tmp_path, {"drape": ALL_STUBS["drape"]})

    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        app = _app(garments_dir, artifacts_dir, adapters)
    with TestClient(app) as client:
        job_id = _post(client).json()["job_id"]
        final = _finish(client, job_id)  # 지연이 적용됐다면 여기서 TimeoutError가 난다

        preview = client.get(f"/api/artifacts/{final.visual.drape_preview_artifact_id}")
        assert _png_size(preview.content) == (2, 2)  # 스텁이 쓴 파일이고 자리표시가 덮어쓰지 않았다
        # 가짜 포트의 산출물에도 자리표시 그림을 만들지 않는다
        assert client.get(f"/api/artifacts/{final.visual.frame_artifact_id}").status_code == 404

    # 스텁 착장이 쓴 두 파일뿐이다. 자리표시 PNG는 만들지 않았다.
    assert sorted(p.name for p in artifacts_dir.iterdir()) == [
        f"{job_id}_drape_preview.png",
        f"{job_id}_drape_result.json",
    ]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "무시" in warnings[0] and "drape" in warnings[0]


def test_no_warning_when_the_fake_options_are_not_set(
    tmp_path, garments_dir, artifacts_dir, monkeypatch, caplog
):
    monkeypatch.delenv(FAKE_PLACEHOLDERS_ENV, raising=False)
    monkeypatch.delenv(FAKE_STAGE_DELAY_ENV, raising=False)

    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        _app(garments_dir, artifacts_dir, _config(tmp_path, ALL_STUBS))

    assert caplog.records == []


@pytest.mark.parametrize("delay", ["0", "0.0", "  ", ""])
def test_a_zero_or_blank_delay_is_not_a_requested_option(
    tmp_path, garments_dir, artifacts_dir, monkeypatch, caplog, delay
):
    monkeypatch.setenv(FAKE_STAGE_DELAY_ENV, delay)

    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        _app(garments_dir, artifacts_dir, _config(tmp_path, ALL_STUBS))

    assert caplog.records == []


def test_fake_only_options_still_apply_when_every_port_is_fake(
    tmp_path, garments_dir, artifacts_dir, monkeypatch
):
    monkeypatch.setenv(FAKE_PLACEHOLDERS_ENV, "1")
    monkeypatch.setenv(FAKE_STAGE_DELAY_ENV, "0.001")
    with TestClient(_app(garments_dir, artifacts_dir, "fake")) as client:
        final = _finish(client, _post(client).json()["job_id"])

        preview = client.get(f"/api/artifacts/{final.visual.drape_preview_artifact_id}")
        assert preview.status_code == 200
        assert _png_size(preview.content) == (384, 512)  # B3 때처럼 자리표시 PNG


def test_bad_fake_option_values_still_fail_when_every_port_is_fake(
    tmp_path, garments_dir, artifacts_dir, monkeypatch
):
    monkeypatch.setenv(FAKE_STAGE_DELAY_ENV, "abc")

    with pytest.raises(ValueError, match=FAKE_STAGE_DELAY_ENV):
        _app(garments_dir, artifacts_dir, "fake")


def test_bad_fake_option_values_are_ignored_when_a_port_is_real(
    tmp_path, garments_dir, artifacts_dir, monkeypatch, caplog
):
    monkeypatch.setenv(FAKE_STAGE_DELAY_ENV, "abc")

    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        app = _app(garments_dir, artifacts_dir, _config(tmp_path, {"body": ALL_STUBS["body"]}))

    assert app.state.runner is not None
    assert any("무시" in r.getMessage() for r in caplog.records)


# --- 시작 로그와 상태 ---


def test_startup_logs_one_line_with_the_adapter_of_each_port(
    tmp_path, garments_dir, artifacts_dir, caplog
):
    adapters = _config(tmp_path, {"drape": ALL_STUBS["drape"]})
    app = _app(garments_dir, artifacts_dir, adapters)

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with TestClient(app):
            pass

    lines = [r.getMessage() for r in caplog.records if "피팅 어댑터" in r.getMessage()]
    assert lines == [
        f"피팅 어댑터: body=fake, drape={STUB}:make_drape, render=fake, realize=fake, validate=fake"
    ]


def test_startup_log_says_when_ports_were_given_directly(garments_dir, artifacts_dir, caplog):
    app = create_app(
        garments_dir=garments_dir,
        artifacts_dir=artifacts_dir,
        ports=fake_ports(fit=RuleFitCalculator()),
    )

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with TestClient(app):
            pass

    assert any("직접 넘긴" in r.getMessage() for r in caplog.records)


def test_app_state_describes_the_adapters(tmp_path, garments_dir, artifacts_dir):
    app = _app(garments_dir, artifacts_dir, _config(tmp_path, ALL_STUBS))

    assert app.state.adapters == ALL_STUBS


# --- 기존 동작: 전부 가짜면 B3 때와 같다 ---


def test_fake_config_gives_the_same_final_status_as_the_contract_example(
    garments_dir, artifacts_dir
):
    with TestClient(_app(garments_dir, artifacts_dir, "fake")) as client:
        first = _post(client).json()
        final = _finish(client, first["job_id"]).model_dump(mode="json")

    expected = json.loads((EXAMPLES / "fitting_status_succeeded.json").read_text(encoding="utf-8"))
    normalized = json.loads(json.dumps(final).replace(first["job_id"], "fit_sample_001"))
    assert normalized == expected


def test_fit_api_does_not_depend_on_the_adapters(tmp_path, garments_dir, artifacts_dir):
    """`POST /api/fit`은 핏 계산기만 쓴다. 영상 어댑터가 어떻든 같다."""
    body = {k: v for k, v in REQUEST_EXAMPLE.items() if k not in ("mode", "realize_consent")}
    with TestClient(_app(garments_dir, artifacts_dir, "fake")) as fake_client:
        fake = fake_client.post("/api/fit", json=body).json()
    with TestClient(_app(garments_dir, artifacts_dir, _config(tmp_path, ALL_STUBS))) as stub_client:
        stub = stub_client.post("/api/fit", json=body).json()

    fake.pop("created_at"), stub.pop("created_at")
    fake.pop("job_id"), stub.pop("job_id")
    assert fake == stub


# --- 모듈의 app은 처음 찾을 때 만든다 ---


def test_module_app_is_created_on_first_use_and_cached(monkeypatch):
    sentinel = object()
    created = []
    monkeypatch.setattr(app_module, "create_app", lambda: created.append(1) or sentinel)
    app_module.__dict__.pop("app", None)
    try:
        assert app_module.app is sentinel
        assert app_module.app is sentinel
        assert created == [1]  # 한 번만 만든다
    finally:
        app_module.__dict__.pop("app", None)


def test_importing_the_module_does_not_read_the_adapter_config(monkeypatch):
    """설정이 틀려도 모듈을 불러오는 것만으로는 실패하지 않는다. `app`을 찾을 때 멈춘다."""
    monkeypatch.setenv("FIT_ADAPTERS", "/없는/파일.yaml")
    app_module.__dict__.pop("app", None)
    try:
        with pytest.raises(AdapterConfigError, match="찾을 수 없습니다"):
            app_module.app  # noqa: B018
        assert "app" not in app_module.__dict__  # 실패한 결과를 남기지 않는다
    finally:
        app_module.__dict__.pop("app", None)


def test_unknown_module_attributes_still_raise_attribute_error():
    with pytest.raises(AttributeError):
        app_module.no_such_attribute  # noqa: B018


def test_uvicorn_style_lookup_builds_the_default_app(monkeypatch):
    monkeypatch.setenv("FIT_ADAPTERS", "fake")
    app_module.__dict__.pop("app", None)
    try:
        app = getattr(app_module, "app")  # noqa: B009 — `uvicorn server.app:app`이 하는 방식
        assert app.state.adapters == ALL_FAKE
    finally:
        app_module.__dict__.pop("app", None)

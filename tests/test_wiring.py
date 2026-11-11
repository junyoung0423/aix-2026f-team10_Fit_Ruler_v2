"""어댑터 교체 구조 `server/wiring.py` (#12 #13).

실제 어댑터가 없으니 `tests/stub_adapters.py`의 스텁을 "모듈:팩토리"로 꽂아서 실제 어댑터와 같은
경로(importlib → 팩토리 → 포트 객체)를 시험한다.
"""

import dataclasses
import logging
import re
from pathlib import Path

import pytest
import yaml

from fitting.adapters.fake import (
    FakeBodyBuilder,
    FakeDraper,
    FakeRealizer,
    FakeRenderer,
    FakeValidator,
)
from fitting.fit import RuleFitCalculator
from server.wiring import (
    ADAPTERS_CONFIG_DIR,
    PORT_NAMES,
    AdapterConfigError,
    AdapterSettings,
    build_ports,
    describe,
    load_adapter_config,
    resolve_config_path,
)

from . import stub_adapters
from .stub_adapters import (
    StubBody,
    StubDraper,
    StubRealizer,
    StubRenderer,
    StubValidator,
)

STUB = "tests.stub_adapters"
ALL_STUBS = {
    "body": f"{STUB}:make_body",
    "drape": f"{STUB}:make_drape",
    "render": f"{STUB}:make_render",
    "realize": f"{STUB}:make_realize",
    "validate": f"{STUB}:make_validate",
}
ALL_FAKE = dict.fromkeys(PORT_NAMES, "fake")


@pytest.fixture(autouse=True)
def _reset_stubs():
    stub_adapters.reset()


def _write(tmp_path: Path, ports: dict | None = None, name: str = "adapters.yaml", **top) -> Path:
    data = {"version": "adapters_v0", "ports": {**ALL_FAKE, **(ports or {})}, **top}
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def _settings(tmp_path: Path, environ: dict | None = None) -> AdapterSettings:
    return AdapterSettings.from_environ(artifacts_dir=tmp_path / "art", environ=environ or {})


def _build(tmp_path: Path, ports: dict):
    config = load_adapter_config(_write(tmp_path, ports))
    return build_ports(config, _settings(tmp_path), fit=RuleFitCalculator())


# --- 설정 파일 ---


def test_default_and_fake_files_are_valid():
    fake = load_adapter_config("fake")
    default = load_adapter_config("default")

    assert fake.version == default.version == "adapters_v0"
    assert list(fake.ports) == list(default.ports) == list(PORT_NAMES)
    assert fake.all_fake()
    # default의 실제 항목은 "모듈:팩토리" 형식만 확인한다. 불러오지 않는다.
    for spec in default.ports.values():
        assert spec == "fake" or re.fullmatch(r"[A-Za-z_][\w.]*:[A-Za-z_]\w*", spec)


def test_fake_yaml_is_all_fake_and_stays_that_way():
    """테스트·CI·시연 연습용이라 내용이 바뀌지 않는다."""
    raw = yaml.safe_load((ADAPTERS_CONFIG_DIR / "fake.yaml").read_text(encoding="utf-8"))

    assert raw == {"version": "adapters_v0", "ports": ALL_FAKE}


def test_port_names_are_the_five_video_stages_in_order():
    assert PORT_NAMES == ("body", "drape", "render", "realize", "validate")


def test_config_remembers_where_it_came_from(tmp_path):
    path = _write(tmp_path)

    assert load_adapter_config(path).source == str(path)


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        (lambda d: d["ports"].pop("render"), "필요한 키가 없습니다: render"),
        (lambda d: d["ports"].update(extra="fake"), "모르는 키가 있습니다: extra"),
        (lambda d: d.update(unknown=1), "모르는 키가 있습니다: unknown"),
        (lambda d: d.pop("version"), "version은 adapters_v0"),
        (lambda d: d.update(version="adapters_v9"), "version은 adapters_v0"),
        (lambda d: d.pop("ports"), "ports가 없거나"),
        (lambda d: d.update(ports=["fake"]), "ports가 없거나"),
        (lambda d: d["ports"].update(drape="no colon here"), "ports.drape: 값 형식이 틀립니다"),
        (lambda d: d["ports"].update(drape="a:b:c"), "ports.drape: 값 형식이 틀립니다"),
        (lambda d: d["ports"].update(drape="1bad:make"), "ports.drape: 값 형식이 틀립니다"),
        (lambda d: d["ports"].update(drape="mod:make-it"), "ports.drape: 값 형식이 틀립니다"),
        (lambda d: d["ports"].update(drape="mod.:make"), "ports.drape: 값 형식이 틀립니다"),
        (lambda d: d["ports"].update(drape=":make"), "ports.drape: 값 형식이 틀립니다"),
        (lambda d: d["ports"].update(drape="mod:"), "ports.drape: 값 형식이 틀립니다"),
        (lambda d: d["ports"].update(drape="Fake"), "ports.drape: 값 형식이 틀립니다"),
        (lambda d: d["ports"].update(drape=""), "ports.drape: 값 형식이 틀립니다"),
        (lambda d: d["ports"].update(drape=5), "ports.drape: 값 형식이 틀립니다"),
        (lambda d: d["ports"].update(drape=True), "ports.drape: 값 형식이 틀립니다"),
        (lambda d: d["ports"].update(drape=None), "ports.drape: 값 형식이 틀립니다"),
    ],
)
def test_invalid_config_is_rejected(tmp_path, change, fragment):
    data = {"version": "adapters_v0", "ports": dict(ALL_FAKE)}
    change(data)
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(AdapterConfigError) as raised:
        load_adapter_config(path)

    assert fragment in str(raised.value)
    assert str(path) in str(raised.value)


def test_several_problems_are_reported_together(tmp_path):
    data = {"version": "x", "ports": {**ALL_FAKE, "drape": "nope", "extra": "fake"}}
    del data["ports"]["render"]
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(AdapterConfigError) as raised:
        load_adapter_config(path)

    message = str(raised.value)
    for fragment in ("version은", "render", "ports.drape", "extra"):
        assert fragment in message


@pytest.mark.parametrize("content", ["", "- fake\n- fake\n", "just text"])
def test_config_that_is_not_a_mapping_is_rejected(tmp_path, content):
    path = tmp_path / "bad.yaml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(AdapterConfigError, match="키-값 묶음"):
        load_adapter_config(path)


def test_broken_yaml_is_rejected(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("ports: [unclosed", encoding="utf-8")

    with pytest.raises(AdapterConfigError, match="읽을 수 없습니다"):
        load_adapter_config(path)


def test_missing_file_is_rejected_with_the_available_names(tmp_path):
    with pytest.raises(AdapterConfigError) as raised:
        load_adapter_config(tmp_path / "없는" / "파일.yaml")
    assert "찾을 수 없습니다" in str(raised.value)
    assert "파일.yaml" in str(raised.value)

    with pytest.raises(AdapterConfigError) as raised:
        load_adapter_config("no_such_name")
    message = str(raised.value)
    assert "no_such_name.yaml" in message
    assert "default" in message and "fake" in message  # config/adapters/의 이름을 알려 준다


def test_error_is_an_exception_with_a_korean_message():
    assert issubclass(AdapterConfigError, Exception)
    assert str(AdapterConfigError("한국어 한 줄")) == "한국어 한 줄"


# --- 고르는 순서: 인자 > FIT_ADAPTERS > default ---


@pytest.mark.parametrize(
    ("value", "expected_name"),
    [("fake", "fake.yaml"), ("default", "default.yaml"), ("my-setup_2", "my-setup_2.yaml")],
)
def test_a_plain_name_means_a_file_in_config_adapters(value, expected_name):
    assert resolve_config_path(value, environ={}) == ADAPTERS_CONFIG_DIR / expected_name


@pytest.mark.parametrize("value", ["sub/dir.yaml", "x.yaml", "x.YML", "a\\b", "/abs/x", "./x"])
def test_a_slash_or_yaml_suffix_means_a_path(value):
    assert resolve_config_path(value, environ={}) == Path(value)


def test_a_path_object_is_always_a_path():
    assert resolve_config_path(Path("fake"), environ={}) == Path("fake")


def test_order_is_argument_then_environment_then_default(tmp_path):
    from_arg = _write(tmp_path, {"drape": f"{STUB}:make_drape"}, name="arg.yaml")
    from_env = _write(tmp_path, {"render": f"{STUB}:make_render"}, name="env.yaml")
    environ = {"FIT_ADAPTERS": str(from_env)}

    assert load_adapter_config(str(from_arg), environ=environ).source == str(from_arg)
    assert load_adapter_config(None, environ=environ).source == str(from_env)
    assert load_adapter_config(None, environ={}).source == str(ADAPTERS_CONFIG_DIR / "default.yaml")


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_values_mean_not_chosen(blank):
    assert resolve_config_path(blank, environ={"FIT_ADAPTERS": "fake"}).name == "fake.yaml"
    assert resolve_config_path(None, environ={"FIT_ADAPTERS": blank}).name == "default.yaml"
    assert resolve_config_path(blank, environ={"FIT_ADAPTERS": blank}).name == "default.yaml"


def test_environment_variable_is_read_from_the_process_by_default(tmp_path, monkeypatch):
    path = _write(tmp_path, {"body": f"{STUB}:make_body"})
    monkeypatch.setenv("FIT_ADAPTERS", str(path))

    assert load_adapter_config().ports["body"] == f"{STUB}:make_body"

    monkeypatch.delenv("FIT_ADAPTERS")
    assert load_adapter_config().source.endswith("default.yaml")


# --- 조립 ---


def test_all_stubs_are_built_from_the_config(tmp_path):
    ports = _build(tmp_path, ALL_STUBS)

    assert isinstance(ports.body, StubBody)
    assert isinstance(ports.drape, StubDraper)
    assert isinstance(ports.render, StubRenderer)
    assert isinstance(ports.realize, StubRealizer)
    assert isinstance(ports.validate, StubValidator)


def test_the_fit_calculator_is_the_one_we_pass_in(tmp_path):
    calculator = RuleFitCalculator()
    config = load_adapter_config(_write(tmp_path, ALL_STUBS))

    ports = build_ports(config, _settings(tmp_path), fit=calculator)

    assert ports.fit is calculator  # 핏 계산기는 설정 대상이 아니다


def test_only_some_ports_can_be_real(tmp_path):
    ports = _build(tmp_path, {"drape": ALL_STUBS["drape"], "validate": ALL_STUBS["validate"]})

    assert isinstance(ports.body, FakeBodyBuilder)
    assert isinstance(ports.drape, StubDraper)
    assert isinstance(ports.render, FakeRenderer)
    assert isinstance(ports.realize, FakeRealizer)
    assert isinstance(ports.validate, StubValidator)


def test_all_fake_builds_the_fake_adapters(tmp_path):
    ports = _build(tmp_path, {})

    assert isinstance(ports.body, FakeBodyBuilder)
    assert isinstance(ports.drape, FakeDraper)
    assert isinstance(ports.render, FakeRenderer)
    assert isinstance(ports.realize, FakeRealizer)
    assert isinstance(ports.validate, FakeValidator)


def test_each_build_creates_new_fake_instances(tmp_path):
    first, second = _build(tmp_path, {}), _build(tmp_path, {})

    assert first.drape is not second.drape  # 호출 기록을 섞지 않는다


def test_factories_get_the_settings_object(tmp_path):
    config = load_adapter_config(_write(tmp_path, ALL_STUBS))
    settings = _settings(tmp_path)

    build_ports(config, settings, fit=RuleFitCalculator())

    assert stub_adapters.SEEN_SETTINGS == [settings] * 5
    assert stub_adapters.CALLS == []  # 팩토리는 부르지만 포트는 부르지 않는다


def test_describe_lists_each_port_in_order(tmp_path):
    config = load_adapter_config(_write(tmp_path, {"drape": ALL_STUBS["drape"]}))

    assert describe(config) == {
        "body": "fake",
        "drape": f"{STUB}:make_drape",
        "render": "fake",
        "realize": "fake",
        "validate": "fake",
    }
    assert list(describe(config)) == list(PORT_NAMES)
    assert not config.all_fake()


# --- 연결 실패는 가짜로 대신하지 않는다 ---


@pytest.mark.parametrize(
    ("spec", "fragment"),
    [
        ("no.such.module:make", "모듈 'no.such.module'을 불러올 수 없습니다"),
        (f"{STUB}:no_such_factory", "'no_such_factory'이 없습니다"),
        (f"{STUB}:NOT_A_FUNCTION", "함수가 아닙니다"),
        (f"{STUB}:make_raises", "팩토리를 부르다 예외가 났습니다 (RuntimeError: 팩토리가 터졌다)"),
        (f"{STUB}:make_config_error", "팩토리가 설정 오류를 알렸습니다: Blender 경로가 없습니다"),
        (f"{STUB}:make_wrong_object", "'drape' 메서드가 없습니다"),
    ],
)
def test_bad_adapter_stops_with_the_port_the_value_and_the_reason(tmp_path, spec, fragment):
    config = load_adapter_config(_write(tmp_path, {"drape": spec}))

    with pytest.raises(AdapterConfigError) as raised:
        build_ports(config, _settings(tmp_path), fit=RuleFitCalculator())

    message = str(raised.value)
    assert fragment in message
    assert "drape" in message  # 어느 포트
    assert repr(spec) in message  # 어느 설정 값
    assert "fake" not in message.split("—", 1)[1]  # 가짜로 대신했다는 말이 없다
    assert str(tmp_path / "adapters.yaml") in message  # 어느 설정 파일


@pytest.mark.parametrize(
    ("port", "method"),
    [
        ("body", "build_body"),
        ("drape", "drape"),
        ("render", "render"),
        ("realize", "realize"),
        ("validate", "validate"),
    ],
)
def test_wrong_object_names_the_missing_method_of_that_port(tmp_path, port, method):
    config = load_adapter_config(_write(tmp_path, {port: f"{STUB}:make_wrong_object"}))

    with pytest.raises(AdapterConfigError, match=f"'{method}' 메서드가 없습니다"):
        build_ports(config, _settings(tmp_path), fit=RuleFitCalculator())


def test_a_port_with_a_method_of_another_port_is_wrong(tmp_path):
    """착장 자리에 몸 어댑터를 꽂으면 drape 메서드가 없어서 거절된다."""
    config = load_adapter_config(_write(tmp_path, {"drape": ALL_STUBS["body"]}))

    with pytest.raises(AdapterConfigError, match="'drape' 메서드가 없습니다"):
        build_ports(config, _settings(tmp_path), fit=RuleFitCalculator())


def test_two_bad_adapters_are_reported_together_in_port_order(tmp_path):
    config = load_adapter_config(
        _write(
            tmp_path,
            {
                "validate": "no.such.module:make",
                "body": ALL_STUBS["body"],
                "render": f"{STUB}:make_config_error",
            },
        )
    )

    with pytest.raises(AdapterConfigError) as raised:
        build_ports(config, _settings(tmp_path), fit=RuleFitCalculator())

    message = str(raised.value)
    assert "render" in message and "validate" in message
    assert message.index("render") < message.index("validate")  # 포트 순서
    assert "no.such.module" in message and "Blender" in message
    assert "body" not in message.split("\n", 1)[1]  # 멀쩡한 포트는 오류에 없다


def test_a_module_that_fails_on_import_is_a_config_error(tmp_path, monkeypatch):
    (tmp_path / "broken_adapter_mod.py").write_text("raise RuntimeError('import boom')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    config = load_adapter_config(_write(tmp_path, {"render": "broken_adapter_mod:make"}))

    with pytest.raises(AdapterConfigError) as raised:
        build_ports(config, _settings(tmp_path), fit=RuleFitCalculator())

    assert "불러올 수 없습니다" in str(raised.value)
    assert "import boom" in str(raised.value)


def test_nothing_is_built_when_one_adapter_is_wrong(tmp_path):
    """틀린 포트가 있으면 부분적으로 만든 묶음을 돌려주지 않고 멈춘다."""
    config = load_adapter_config(
        _write(tmp_path, {"body": ALL_STUBS["body"], "drape": "no.such.module:make"})
    )

    with pytest.raises(AdapterConfigError):
        build_ports(config, _settings(tmp_path), fit=RuleFitCalculator())


# --- 설정 객체 (AdapterSettings) ---


def test_blender_path_comes_before_blender_exe(tmp_path):
    settings = _settings(tmp_path, {"BLENDER_PATH": "/a/blender", "BLENDER_EXE": "/b/blender"})

    assert settings.blender_path == Path("/a/blender")


def test_blender_exe_is_the_fallback(tmp_path):
    assert _settings(tmp_path, {"BLENDER_EXE": "/b/blender"}).blender_path == Path("/b/blender")
    # 비어 있는 BLENDER_PATH는 정하지 않은 것으로 본다
    settings = _settings(tmp_path, {"BLENDER_PATH": "  ", "BLENDER_EXE": "/b/blender"})
    assert settings.blender_path == Path("/b/blender")


def test_unset_values_are_none_or_default(tmp_path):
    settings = _settings(tmp_path, {"BLENDER_PATH": "", "SMPL_MODEL_DIR": " "})

    assert settings.blender_path is None
    assert settings.smpl_model_dir is None
    assert settings.fal_key_present is False
    assert settings.artifacts_dir == tmp_path / "art"
    assert settings.work_dir.parts[-2:] == ("artifacts", "work")  # 기본 artifacts/work/
    assert settings.repo_root == Path(__file__).resolve().parents[1]


def test_smpl_and_work_dir_come_from_the_environment(tmp_path):
    settings = _settings(tmp_path, {"SMPL_MODEL_DIR": "/models/smpl", "FIT_WORK_DIR": "/w"})

    assert settings.smpl_model_dir == Path("/models/smpl")
    assert settings.work_dir == Path("/w")


def test_settings_read_the_process_environment_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("BLENDER_PATH", "/from/process")

    settings = AdapterSettings.from_environ(artifacts_dir=tmp_path)

    assert settings.blender_path == Path("/from/process")


def test_settings_are_frozen(tmp_path):
    with pytest.raises(dataclasses.FrozenInstanceError):
        _settings(tmp_path).artifacts_dir = tmp_path  # type: ignore[misc]


SECRET = "sk-super-secret-fal-key-12345"


def test_fal_key_is_only_a_flag_and_never_the_value(tmp_path):
    settings = _settings(tmp_path, {"FAL_KEY": SECRET})

    assert settings.fal_key_present is True
    assert SECRET not in repr(settings)
    assert SECRET not in str(settings)
    assert SECRET not in str(dataclasses.asdict(settings))
    assert [f.name for f in dataclasses.fields(settings)] == [
        "artifacts_dir",
        "work_dir",
        "blender_path",
        "smpl_model_dir",
        "fal_key_present",
        "repo_root",
    ]


def test_a_blank_fal_key_counts_as_missing(tmp_path):
    assert _settings(tmp_path, {"FAL_KEY": ""}).fal_key_present is False
    assert _settings(tmp_path, {"FAL_KEY": "   "}).fal_key_present is False


def test_fal_key_does_not_leak_into_logs_or_errors(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("FAL_KEY", SECRET)
    config = load_adapter_config(
        _write(tmp_path, {"body": ALL_STUBS["body"], "drape": f"{STUB}:make_raises"})
    )

    with caplog.at_level(logging.DEBUG):
        settings = AdapterSettings.from_environ(artifacts_dir=tmp_path)
        with pytest.raises(AdapterConfigError) as raised:
            build_ports(config, settings, fit=RuleFitCalculator())

    assert settings.fal_key_present is True
    assert SECRET not in str(raised.value)
    assert SECRET not in caplog.text
    assert SECRET not in repr(config)

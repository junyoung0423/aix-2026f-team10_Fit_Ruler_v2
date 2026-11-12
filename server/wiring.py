"""어댑터 교체 구조 (#12 #13). 영상 단계 5개를 가짜 또는 실제 어댑터로 포트별로 고른다.

설정 파일 `config/adapters/*.yaml` 한 줄이 포트 하나의 어댑터를 정한다.

    version: adapters_v0
    ports:
      body: fake                                   # 가짜 어댑터
      drape: "my.package.drape:make_draper"        # "모듈:팩토리" — 실제 어댑터

값은 `fake` 또는 `"파이썬.모듈.경로:팩토리이름"`이다. C·D는 자기 어댑터가 머지될 때 이 설정 한 줄만
바꾸면 되고 `server/`를 고치지 않는다. 팩토리 약속과 연결 방법은 `docs/adapters.md`에 있다.

**설정이 틀리면 가짜로 대신하지 않는다.** 모듈이나 팩토리가 없거나, 팩토리가 예외를 던지거나, 돌려준
객체에 필요한 메서드가 없으면 `AdapterConfigError`로 멈춘다. 가짜로 조용히 바꾸면 실패가 성공처럼
보이기 때문이다(#12). 여러 개가 틀리면 한 번에 모아서 알려 준다.

핏 계산기는 설정 대상이 아니다. 호출하는 쪽이 항상 실제 `RuleFitCalculator`를 `fit=`으로 넘긴다.
"""

import importlib
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from fitting.adapters.fake import (
    FakeBodyBuilder,
    FakeDraper,
    FakeRealizer,
    FakeRenderer,
    FakeValidator,
)
from fitting.ports import FitCalculator
from fitting.services.fitting import FittingPorts

REPO_ROOT = Path(__file__).resolve().parents[1]
ADAPTERS_CONFIG_DIR = REPO_ROOT / "config" / "adapters"
ADAPTERS_ENV = "FIT_ADAPTERS"
WORK_DIR_ENV = "FIT_WORK_DIR"
DEFAULT_WORK_DIR = REPO_ROOT / "artifacts" / "work"
DEFAULT_ADAPTERS_NAME = "default"

SUPPORTED_VERSION = "adapters_v0"
FAKE = "fake"

# 영상 단계 5개: 포트 이름 → (가짜 구현, 어댑터가 가져야 하는 메서드 이름)
_PORTS: dict[str, tuple[Callable[[], Any], str]] = {
    "body": (FakeBodyBuilder, "build_body"),
    "drape": (FakeDraper, "drape"),
    "render": (FakeRenderer, "render"),
    "realize": (FakeRealizer, "realize"),
    "validate": (FakeValidator, "validate"),
}
PORT_NAMES = tuple(_PORTS)

_TOP_KEYS = {"version", "ports"}


class AdapterConfigError(Exception):
    """어댑터 설정이나 연결이 틀렸다. 메시지는 사람이 읽는 한국어다.

    팩토리는 필요한 것이 없을 때(예: Blender 경로 없음) 이 예외를 던진다.
    """


@dataclass(frozen=True)
class AdapterSettings:
    """팩토리가 받는 환경 정보. `FAL_KEY`의 값은 담지 않는다(로그·출력에 새지 않게).

    어댑터는 산출물을 `{artifact_id}.{확장자}`로 `artifacts_dir`에 쓴다. 중간 파일(.blend, 메시
    등)은 `work_dir` 아래에 두고, 작업별 하위 폴더는 어댑터가 `request.job_id`로 만든다.
    """

    artifacts_dir: Path
    work_dir: Path
    blender_path: Path | None
    smpl_model_dir: Path | None
    fal_key_present: bool  # 값이 아니라 있는지만. 필요하면 어댑터가 환경변수 FAL_KEY를 직접 읽는다
    repo_root: Path

    @classmethod
    def from_environ(
        cls,
        *,
        artifacts_dir: Path | str,
        environ: Mapping[str, str] | None = None,
        repo_root: Path = REPO_ROOT,
    ) -> "AdapterSettings":
        """환경변수에서 읽는다. 비어 있는 값은 없는 것으로 본다.

        - `FIT_WORK_DIR` (기본 `artifacts/work/`)
        - `BLENDER_PATH`, 없으면 `BLENDER_EXE` (기존 C 코드가 `BLENDER_EXE`를 쓴다)
        - `SMPL_MODEL_DIR`
        - `FAL_KEY`는 비어 있지 않은지만
        """
        env = os.environ if environ is None else environ

        def value(name: str) -> str | None:
            raw = env.get(name)
            return raw.strip() if raw is not None and raw.strip() else None

        blender = value("BLENDER_PATH") or value("BLENDER_EXE")
        smpl = value("SMPL_MODEL_DIR")
        work = value(WORK_DIR_ENV)
        return cls(
            artifacts_dir=Path(artifacts_dir),
            work_dir=Path(work) if work else repo_root / "artifacts" / "work",
            blender_path=Path(blender) if blender else None,
            smpl_model_dir=Path(smpl) if smpl else None,
            fal_key_present=value("FAL_KEY") is not None,
            repo_root=repo_root,
        )


@dataclass(frozen=True)
class AdapterConfig:
    """읽어서 검증한 어댑터 설정. `ports`는 포트 이름 → `fake` 또는 `모듈:팩토리`다."""

    version: str
    ports: Mapping[str, str]
    source: str  # 읽은 파일. 오류 메시지와 출력에 쓴다

    def all_fake(self) -> bool:
        return all(spec == FAKE for spec in self.ports.values())


def _report(header: str, problems: list[str]) -> AdapterConfigError:
    """문제가 하나면 한 줄, 여럿이면 목록으로 적은 오류."""
    if len(problems) == 1:
        return AdapterConfigError(f"{header}: {problems[0]}")
    lines = "\n".join(f"  - {problem}" for problem in problems)
    return AdapterConfigError(f"{header}:\n{lines}")


# --- 설정 읽기 ----------------------------------------------------------------------------------


def _looks_like_path(value: str) -> bool:
    return "/" in value or "\\" in value or value.lower().endswith((".yaml", ".yml"))


def _available_names(config_dir: Path) -> list[str]:
    return sorted(p.stem for p in config_dir.glob("*.yaml")) if config_dir.is_dir() else []


def resolve_config_path(
    name_or_path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_dir: Path = ADAPTERS_CONFIG_DIR,
) -> Path:
    """설정 파일 위치. 함수 인자 > 환경변수 `FIT_ADAPTERS` > `default` 순서로 고른다.

    `default`·`fake` 같은 이름이면 `config/adapters/{이름}.yaml`, `/`가 있거나 `.yaml`로 끝나면
    그 경로의 파일이다. 비어 있는 값은 정하지 않은 것으로 본다.
    """
    env = os.environ if environ is None else environ
    if isinstance(name_or_path, Path):
        return name_or_path
    chosen = (name_or_path or "").strip() or (env.get(ADAPTERS_ENV) or "").strip()
    chosen = chosen or DEFAULT_ADAPTERS_NAME
    if _looks_like_path(chosen):
        return Path(chosen)
    return config_dir / f"{chosen}.yaml"


def load_adapter_config(
    name_or_path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_dir: Path = ADAPTERS_CONFIG_DIR,
) -> AdapterConfig:
    """설정 파일을 읽고 형식(버전, 키, 값 형식)을 검증한다.

    Raises:
        AdapterConfigError: 파일이 없거나, YAML이 아니거나, 형식이 맞지 않는다. 형식 문제는 모두
            모아서 한 번에 알린다.
    """
    path = resolve_config_path(name_or_path, environ=environ, config_dir=config_dir)
    if not path.is_file():
        names = ", ".join(_available_names(config_dir)) or "없음"
        raise AdapterConfigError(
            f"어댑터 설정 파일을 찾을 수 없습니다: {path} "
            f"(--adapters나 {ADAPTERS_ENV}로 고르며, config/adapters/의 이름: {names})"
        )
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise AdapterConfigError(
            f"어댑터 설정 파일을 읽을 수 없습니다: {path} ({error})"
        ) from error

    problems: list[str] = []
    version = ""
    specs: dict[str, str] = {}
    if not isinstance(data, Mapping):
        problems.append("키-값 묶음이어야 합니다 (version, ports)")
    else:
        unknown = sorted(str(k) for k in set(data) - _TOP_KEYS)
        if unknown:
            problems.append(f"모르는 키가 있습니다: {', '.join(unknown)}")
        version, specs = _check_fields(data, problems)
    if problems:
        raise _report(f"어댑터 설정 파일이 맞지 않습니다({path})", problems)
    return AdapterConfig(version=version, ports=specs, source=str(path))


def _check_fields(data: Mapping, problems: list[str]) -> tuple[str, dict[str, str]]:
    version = data.get("version")
    if version != SUPPORTED_VERSION:
        problems.append(f"version은 {SUPPORTED_VERSION}이어야 합니다: {version!r}")
    raw_ports = data.get("ports")
    specs: dict[str, str] = {}
    if not isinstance(raw_ports, Mapping):
        problems.append("ports가 없거나 키-값 묶음이 아닙니다")
        return str(version), specs
    missing = [name for name in PORT_NAMES if name not in raw_ports]
    if missing:
        problems.append(f"ports에 필요한 키가 없습니다: {', '.join(missing)}")
    unknown = sorted(str(k) for k in set(raw_ports) - set(PORT_NAMES))
    if unknown:
        problems.append(
            f"ports에 모르는 키가 있습니다: {', '.join(unknown)} (가능: {', '.join(PORT_NAMES)})"
        )
    for name in PORT_NAMES:
        if name not in raw_ports:
            continue
        spec = raw_ports[name]
        if not _is_valid_spec(spec):
            problems.append(
                f"ports.{name}: 값 형식이 틀립니다: {spec!r} "
                f"('{FAKE}' 또는 '파이썬.모듈.경로:팩토리이름'이어야 합니다)"
            )
        else:
            specs[name] = spec
    return str(version), specs


def _split_spec(spec: Any) -> tuple[str, str] | None:
    """`모듈:팩토리` → (모듈, 팩토리). `fake`나 형식이 틀리면 None."""
    if not isinstance(spec, str) or spec == FAKE or spec.count(":") != 1:
        return None
    module, factory = spec.split(":")
    names = module.split(".")
    if not all(part.isidentifier() for part in names) or not factory.isidentifier():
        return None
    return module, factory


def _is_valid_spec(spec: Any) -> bool:
    """`fake` 또는 `모듈:팩토리` 형식인가."""
    return spec == FAKE or _split_spec(spec) is not None


def describe(config: AdapterConfig) -> dict[str, str]:
    """포트별로 `fake` 또는 팩토리 경로. 서버 시작 로그와 실행 스크립트 출력에 쓴다."""
    return {name: config.ports[name] for name in PORT_NAMES}


# --- 조립 ---------------------------------------------------------------------------------------


def build_ports(
    config: AdapterConfig, settings: AdapterSettings, *, fit: FitCalculator
) -> FittingPorts:
    """설정대로 영상 포트 5개를 만들어 `FittingPorts`로 묶는다.

    `fake`는 가짜 어댑터, 그 밖은 `importlib`로 모듈을 불러 팩토리를 `settings`와 함께 부른다.
    실패하면 가짜로 대신하지 않고 `AdapterConfigError`로 올린다. 어느 포트의 어느 설정 값이 왜
    안 됐는지 한국어로 적고, 틀린 포트가 여럿이면 모두 모은다.
    """
    built: dict[str, Any] = {}
    problems: list[str] = []
    for name in PORT_NAMES:
        spec = config.ports[name]
        if spec == FAKE:
            built[name] = _PORTS[name][0]()
            continue
        try:
            built[name] = _from_factory(name, spec, settings)
        except AdapterConfigError as error:
            problems.append(f"{name}: 설정 값 {spec!r} — {error}")
    if problems:
        raise _report(f"어댑터를 연결하지 못했습니다(설정: {config.source})", problems)
    return FittingPorts(fit=fit, **built)


def _from_factory(name: str, spec: str, settings: AdapterSettings) -> Any:
    split = _split_spec(spec)
    assert split is not None  # load_adapter_config가 형식을 이미 확인했다
    module_name, factory_name = split
    try:
        module = importlib.import_module(module_name)
    except Exception as error:  # 모듈 안의 오류(문법, import 실패)도 설정 문제로 알린다
        raise AdapterConfigError(
            f"모듈 '{module_name}'을 불러올 수 없습니다 ({type(error).__name__}: {error})"
        ) from error
    factory = getattr(module, factory_name, None)
    if factory is None:
        raise AdapterConfigError(f"모듈 '{module_name}'에 '{factory_name}'이 없습니다")
    if not callable(factory):
        raise AdapterConfigError(f"'{module_name}:{factory_name}'은 함수가 아닙니다")
    try:
        adapter = factory(settings)
    except AdapterConfigError as error:
        raise AdapterConfigError(f"팩토리가 설정 오류를 알렸습니다: {error}") from error
    except Exception as error:
        raise AdapterConfigError(
            f"팩토리를 부르다 예외가 났습니다 ({type(error).__name__}: {error})"
        ) from error
    method = _PORTS[name][1]
    if not callable(getattr(adapter, method, None)):
        raise AdapterConfigError(
            f"팩토리가 돌려준 객체({type(adapter).__name__})에 '{method}' 메서드가 없습니다"
        )
    return adapter

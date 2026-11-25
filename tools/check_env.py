"""환경 점검 (#13, B6): 고른 어댑터 설정으로 피팅을 돌리는 데 필요한 것만 점검해 표로 보여 준다.

    python -m tools.check_env                                  # FIT_ADAPTERS > default
    python -m tools.check_env --adapters drape_render_blender  # 착장·렌더만 실제
    python -m tools.check_env --adapters visual_real           # 영상 경로 5단계 모두 실제
    python -m tools.check_env --adapters visual_real --garment-id tee_relaxed_l_black_v1

`tools.run_fitting_once`·서버와 같은 순서로 설정을 고르고(`--adapters` > `FIT_ADAPTERS` >
`default`), 같은 `.env`(`server/env_file.py`)를 읽는다. 항상 보는 것과, 설정에서 실제 어댑터를
고른 포트가 필요로 하는 것만 본다.

- 항상: Python 3.10 이상, 기본 패키지, `.env`, 어댑터 설정, 실패 처리 설정, 의류(`--garment-id`),
  경로(한글 경로 주의)
- 몸(SMPL): 선택 의존성 `body`, `SMPL_MODEL_DIR` 폴더와 중성 모델 파일(**있는지만**, 읽지 않음)
- 착장(Blender): Blender(`BLENDER_PATH`, 없으면 `BLENDER_EXE`), GarmentCode용 Python 3.9,
  GarmentCode 루트, 준비된 의류(`prep.json`), 몸이 가짜면 개발용 몸 OBJ(`FIT_DRAPE_BODY_OBJ`)
- 렌더(Blender): Blender
- 실사화(fal): 선택 의존성 `realize`, `FAL_KEY` (**값은 출력·기록하지 않고 있음/없음만**)
- 검증: 선택 의존성 `realize`(OpenCV)
- 웹 화면: Node 20 이상, `web/node_modules` (피팅 명령에는 필요 없어 "주의"까지만)

위가 모두 통과하면 마지막으로 서버와 같은 팩토리(`server.wiring.build_ports`)로 어댑터를 실제로
만들어 본다. 팩토리의 설정 검사를 그대로 쓰는 것이라, 이것이 통과하면 `run_fitting_once`도 같은
설정으로 시작한다(SMPL 모델은 이때 한 번 읽는다). 외부 서비스(fal)는 부르지 않는다.

상태: 통과 / 없음(고쳐야 돈다) / 주의(돌지만 확인할 것) / 참고.
빠진 것마다 고치는 방법을 표 아래에 한 줄씩 적는다.

종료 코드: 0 필요한 것이 모두 있음("주의"는 있을 수 있음), 1 빠진 것이 있음, 2 사용법 오류.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from fitting.adapters.blender.draper import (
    BODY_OBJ_ENV,
    DEFAULT_GC_PYTHON,
    DEFAULT_GC_ROOT,
    DEFAULT_PREPARED_DIR,
    GC_PYTHON_ENV,
    GC_ROOT_ENV,
    PREPARED_DIR_ENV,
)
from fitting.fit import RuleFitCalculator
from fitting.services.pipeline import (
    PIPELINE_CONFIG_ENV,
    PipelineConfigError,
    load_run_settings,
)
from server.app import DEFAULT_GARMENTS_DIR, GARMENTS_DIR_ENV
from server.env_file import ENV_FILE_ENV, EnvFileResult, load_env_file
from server.garments import GarmentDirectory
from server.wiring import (
    ADAPTERS_ENV,
    FAKE,
    PORT_NAMES,
    AdapterConfig,
    AdapterConfigError,
    AdapterSettings,
    build_ports,
    load_adapter_config,
)
from tools.console import use_utf8_output

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GARMENT_ID = "tee_regular_m_navy_v1"  # tools.run_fitting_once의 기준 사례

OK, MISSING, WARN, INFO = "통과", "없음", "주의", "참고"
EXIT_OK, EXIT_MISSING = 0, 1

MIN_PYTHON = (3, 10)
GC_PYTHON_VERSION = "3.9"
BLENDER_MAJOR = "5"
MIN_NODE_MAJOR = 20  # CI(.github/workflows/ci.yml)의 Node 버전

INSTALL_ALL = 'pip install -e ".[dev,garment,realize,body]"'
SMPL_SUFFIXES = (".pkl", ".npz")  # fitting.adapters.smpl.model.find_neutral_model과 같은 규칙

# 선택 의존성(pyproject.toml): 이름 → import 이름
EXTRAS: dict[str, tuple[str, ...]] = {
    "body": ("numpy", "scipy", "cv2"),
    "realize": ("fal_client", "httpx", "cv2", "numpy"),
}
BASE_MODULES = ("pydantic", "yaml", "fastapi", "uvicorn")

# 실제 어댑터 모듈 → 필요한 것. 모르는 모듈(내 설정의 스텁 등)은 팩토리 검사만 한다.
NEED_SMPL, NEED_BLENDER, NEED_GC, NEED_PREPARED, NEED_FAL_KEY = (
    "smpl",
    "blender",
    "garmentcode",
    "prepared",
    "fal_key",
)
ADAPTER_NEEDS: tuple[tuple[str, frozenset[str]], ...] = (
    ("fitting.adapters.smpl.", frozenset({"extra:body", NEED_SMPL})),
    ("fitting.adapters.blender.draper", frozenset({NEED_BLENDER, NEED_GC, NEED_PREPARED})),
    ("fitting.adapters.blender.renderer", frozenset({NEED_BLENDER})),
    ("fitting.adapters.fal_ltx.", frozenset({"extra:realize", NEED_FAL_KEY})),
    ("fitting.adapters.fal_fitroom.", frozenset({"extra:realize", NEED_FAL_KEY})),
    ("fitting.adapters.media.", frozenset({"extra:realize"})),
)

Probe = Callable[[Sequence[str]], "str | None"]


@dataclass(frozen=True)
class Check:
    item: str
    status: str
    detail: str
    fix: str = ""


# --- 외부 명령 -----------------------------------------------------------------------------------


def probe_version(command: Sequence[str], timeout_s: float = 60.0) -> str | None:
    """`명령 --version`의 첫 줄. 실행할 수 없거나 실패하면 None."""
    try:
        done = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    for line in (done.stdout or "").splitlines() + (done.stderr or "").splitlines():
        if line.strip():
            return line.strip()
    return None


# --- 무엇이 필요한가 ------------------------------------------------------------------------------


def needs_for(config: AdapterConfig) -> set[str]:
    """설정에서 실제 어댑터를 고른 포트가 필요로 하는 것."""
    needs: set[str] = set()
    for spec in config.ports.values():
        if spec == FAKE:
            continue
        for prefix, items in ADAPTER_NEEDS:
            if spec.startswith(prefix):
                needs |= items
    return needs


def _value(environ: Mapping[str, str], name: str) -> str | None:
    raw = environ.get(name)
    return raw.strip() if raw is not None and raw.strip() else None


def _env_path(environ: Mapping[str, str], name: str, default: Path) -> Path:
    raw = _value(environ, name)
    return Path(raw) if raw else default


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _non_ascii(path: Path | str) -> bool:
    return any(ord(ch) > 127 for ch in str(path))


# --- 항상 보는 것 --------------------------------------------------------------------------------


def check_python(version_info: Sequence[int] = sys.version_info) -> Check:
    shown = ".".join(str(part) for part in version_info[:3])
    if tuple(version_info[:2]) >= MIN_PYTHON:
        return Check("Python", OK, f"{shown} (3.10 이상)")
    return Check(
        "Python",
        MISSING,
        f"{shown} (3.10 이상 필요)",
        "Python 3.10 이상으로 저장소 .venv를 다시 만드세요(README 개발 환경)",
    )


def check_base_packages(has_module: Callable[[str], bool] = _has_module) -> Check:
    missing = [name for name in BASE_MODULES if not has_module(name)]
    if not missing:
        return Check("기본 패키지", OK, ", ".join(BASE_MODULES))
    return Check("기본 패키지", MISSING, f"없음: {', '.join(missing)}", INSTALL_ALL)


def check_extra(extra: str, has_module: Callable[[str], bool] = _has_module) -> Check:
    modules = EXTRAS[extra]
    missing = [name for name in modules if not has_module(name)]
    item = f"선택 의존성 {extra}"
    if not missing:
        return Check(item, OK, ", ".join(modules))
    return Check(item, MISSING, f"없음: {', '.join(missing)}", INSTALL_ALL)


def check_env_file(result: EnvFileResult) -> Check:
    """`.env` 읽은 결과. 변수 이름만 적고 값은 적지 않는다."""
    if result.disabled:
        return Check(".env", INFO, f"읽지 않음({ENV_FILE_ENV}=off)")
    if not result.found:
        return Check(
            ".env",
            INFO,
            f"없음: {result.path} (환경변수만 씀)",
        )
    names = f": {', '.join(result.loaded)}" if result.loaded else ""
    parts = [f"{len(result.loaded)}개 읽음{names}"]
    if result.kept:
        parts.append(f"환경변수가 우선: {', '.join(result.kept)}")
    if result.ignored_lines:
        lines = ", ".join(str(n) for n in result.ignored_lines)
        return Check(
            ".env",
            WARN,
            "; ".join(parts) + f"; 형식이 틀린 줄: {lines}",
            ".env는 한 줄에 `이름=값`으로 적습니다(.env.example 참고)",
        )
    return Check(".env", OK, "; ".join(parts))


def check_paths(repo_root: Path, temp_dir: str) -> list[Check]:
    """한글 등 ASCII가 아닌 경로 주의(Windows의 OpenCV·Blender·C 확장이 못 여는 일이 있다)."""
    checks = []
    if _non_ascii(repo_root):
        checks.append(
            Check(
                "저장소 경로",
                WARN,
                f"ASCII가 아닌 글자가 있음: {repo_root}",
                r"저장소를 C:\dev\... 처럼 영어·공백 없는 경로에 두세요",
            )
        )
    if _non_ascii(temp_dir):
        checks.append(
            Check(
                "임시 폴더",
                WARN,
                f"ASCII가 아닌 글자가 있음: {temp_dir} (pytest 임시 폴더에서 cv2가 실패)",
                "pytest는 --basetemp=C:/dev/_pt/pt 처럼 영어 경로를 주고 돌리세요",
            )
        )
    return checks


def check_pipeline(environ: Mapping[str, str]) -> Check:
    try:
        load_run_settings(environ=environ)
    except PipelineConfigError as error:
        return Check(
            "실패 처리 설정",
            MISSING,
            str(error),
            f"{PIPELINE_CONFIG_ENV}를 비우거나 맞는 파일로 고치세요(config/pipeline_v0.yaml)",
        )
    source = _value(environ, PIPELINE_CONFIG_ENV) or "config/pipeline_v0.yaml"
    return Check("실패 처리 설정", OK, source)


def check_garment(environ: Mapping[str, str], garment_id: str) -> Check:
    directory = _env_path(environ, GARMENTS_DIR_ENV, DEFAULT_GARMENTS_DIR)
    item = f"의류 {garment_id}"
    if not directory.is_dir():
        return Check(
            item,
            MISSING,
            f"의류 폴더가 없습니다: {directory}",
            f"{GARMENTS_DIR_ENV}를 비우거나(기본 data/garments) 있는 폴더로 고치세요",
        )
    garment = GarmentDirectory(directory).get(garment_id)
    if garment is None:
        return Check(
            item,
            MISSING,
            f"{directory}에 없음",
            "--garment-id로 등록된 의류를 고르거나 tools.register_aihub_garment로 등록하세요",
        )
    return Check(item, OK, f"{directory} ({garment.status.value})")


# --- 실제 어댑터가 필요로 하는 것 ---------------------------------------------------------


def check_blender(environ: Mapping[str, str], probe: Probe) -> Check:
    path_value = _value(environ, "BLENDER_PATH")
    exe_value = _value(environ, "BLENDER_EXE")
    raw = path_value or exe_value
    fix_set = (
        r"BLENDER_PATH에 Blender 5.2 실행 파일을 적으세요(.env 또는 환경변수, 예: "
        r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe)"
    )
    if raw is None:
        return Check("Blender", MISSING, "BLENDER_PATH가 비어 있음", fix_set)
    path = Path(raw)
    if not path.is_file():
        return Check("Blender", MISSING, f"실행 파일이 없음: {path}", fix_set)
    version = probe([str(path), "--version"])
    detail = f"{path} ({version or '버전을 읽지 못함'})"
    if path_value is None:
        return Check(
            "Blender",
            WARN,
            detail + " — BLENDER_EXE를 읽음",
            "BLENDER_EXE 대신 BLENDER_PATH를 쓰세요",
        )
    if version is None or not version.startswith(f"Blender {BLENDER_MAJOR}."):
        return Check(
            "Blender", WARN, detail, "Blender 5.2로 맞춰 확인한 코드입니다(docs/render.md)"
        )
    return Check("Blender", OK, detail)


def check_gc_python(environ: Mapping[str, str], probe: Probe) -> Check:
    path = _env_path(environ, GC_PYTHON_ENV, DEFAULT_GC_PYTHON)
    item = "GarmentCode용 Python"
    fix = (
        "docs/spikes/garmentcode-blender.md 6절대로 Python 3.9 가상환경을 만들고 "
        f"{GC_PYTHON_ENV}에 그 python.exe를 적으세요"
    )
    if not path.is_file():
        return Check(item, MISSING, f"없음: {path}", fix)
    version = probe([str(path), "--version"])
    detail = f"{path} ({version or '버전을 읽지 못함'})"
    if version is None or not version.startswith(f"Python {GC_PYTHON_VERSION}."):
        hint = f"GarmentCode 코드는 Python {GC_PYTHON_VERSION}에서 확인했습니다"
        return Check(item, WARN, detail, hint)
    return Check(item, OK, detail)


def check_gc_root(environ: Mapping[str, str]) -> Check:
    root = _env_path(environ, GC_ROOT_ENV, DEFAULT_GC_ROOT)
    if (root / "assets" / "garment_programs").is_dir():
        return Check("GarmentCode 루트", OK, str(root))
    return Check(
        "GarmentCode 루트",
        MISSING,
        f"assets/garment_programs가 없음: {root}",
        "GarmentCode를 받아(docs/spikes/garmentcode-blender.md 6절) "
        f"{GC_ROOT_ENV}에 그 폴더를 적으세요",
    )


def check_prepared(environ: Mapping[str, str], garment_id: str) -> Check:
    """착장 어댑터가 읽는 준비 기록(`{FIT_PREPARED_DIR}/{garment_id}/prep.json`)."""
    directory = _env_path(environ, PREPARED_DIR_ENV, DEFAULT_PREPARED_DIR)
    item = f"준비된 의류 {garment_id}"
    fix = f"python -m tools.prepare_garments --only {garment_id} 로 준비하세요(README 사전 준비)"
    record_path = directory / garment_id / "prep.json"
    if not record_path.is_file():
        return Check(item, MISSING, f"준비 기록 없음: {record_path}", fix)
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return Check(item, MISSING, f"준비 기록을 읽지 못함: {record_path} ({error})", fix)
    status = record.get("status") if isinstance(record, dict) else None
    if status != "READY":
        reason = record.get("failure_reason") if isinstance(record, dict) else None
        return Check(item, MISSING, f"준비 실패({status}, {reason}): {record_path}", fix)
    return Check(item, OK, f"{record_path.parent} (견본 {record.get('sample_id')})")


def check_dev_body(environ: Mapping[str, str], body_is_fake: bool) -> Check | None:
    """몸이 가짜면 메시 파일이 없어서 착장에 개발용 몸 OBJ가 꼭 필요하다(docs/adapters.md)."""
    raw = _value(environ, BODY_OBJ_ENV)
    item = "개발용 몸 OBJ"
    example = r"C:\dev\GarmentCode\assets\bodies\mean_all.obj"
    if raw is None:
        if body_is_fake:
            return Check(
                item,
                MISSING,
                f"{BODY_OBJ_ENV}가 비어 있음 (몸 단계가 가짜라 몸 메시가 없음)",
                f"{BODY_OBJ_ENV}에 개발·시연용 몸을 적으세요(예: {example})",
            )
        return None
    path = Path(raw)
    if not path.is_file():
        hint = f"{BODY_OBJ_ENV}를 있는 OBJ로 고치거나 비우세요"
        return Check(item, MISSING, f"없음: {path}", hint)
    if not body_is_fake:
        return Check(
            item,
            WARN,
            f"{path} — SMPL 몸 대신 이 몸으로 착장함",
            f"사용자 몸으로 착장하려면 {BODY_OBJ_ENV}를 비우세요",
        )
    return Check(item, OK, f"{path} (개발·시연용, 사용자 몸이 아님)")


def check_smpl(environ: Mapping[str, str], repo_root: Path) -> Check:
    """SMPL 중성 모델 파일이 있는지만 본다(읽는 것은 마지막 팩토리 검사)."""
    raw = _value(environ, "SMPL_MODEL_DIR")
    fix = (
        "docs/spikes/smpl-body-measure.md 4절대로 각자 SMPL 공식 사이트에서 받아 "
        "models/smpl/에 두고 SMPL_MODEL_DIR=models/smpl 을 적으세요(라이선스상 공유·커밋 금지)"
    )
    if raw is None:
        return Check("SMPL 모델", MISSING, "SMPL_MODEL_DIR이 비어 있음", fix)
    directory = Path(raw)
    # 팩토리와 같게, 상대 경로가 지금 폴더에 없으면 저장소 루트 기준으로 본다
    if not directory.is_absolute() and not directory.is_dir():
        directory = repo_root / directory
    if not directory.is_dir():
        return Check("SMPL 모델", MISSING, f"폴더가 없음: {directory}", fix)
    models = sorted(
        path.name
        for path in directory.iterdir()
        if path.is_file()
        and path.suffix.lower() in SMPL_SUFFIXES
        and "neutral" in path.name.lower()
    )
    if not models:
        return Check(
            "SMPL 모델", MISSING, f"중성 모델(이름에 neutral, .pkl·.npz)이 없음: {directory}", fix
        )
    return Check("SMPL 모델", OK, f"{directory} ({models[0]})")


def check_fal_key(environ: Mapping[str, str]) -> Check:
    """**키 값은 절대 출력하지 않는다.** 있는지만."""
    if _value(environ, "FAL_KEY") is not None:
        return Check("FAL_KEY", OK, "있음 (값은 출력하지 않음, 실제 실사화는 호출마다 비용)")
    return Check(
        "FAL_KEY",
        MISSING,
        "없음",
        "fal API 키를 .env의 FAL_KEY=에 적으세요(.env는 커밋하지 않음, 키는 팀 담당자에게 받음)",
    )


def check_node(repo_root: Path, probe: Probe, which: Callable[[str], str | None]) -> list[Check]:
    """웹 화면(web/)용. 피팅 명령에는 필요 없어 '주의'까지만 낸다."""
    node = which("node")
    if node is None:
        return [
            Check(
                "Node (웹)",
                WARN,
                "없음 — 웹 화면을 띄울 때만 필요",
                f"Node {MIN_NODE_MAJOR} 이상을 설치하고 web/에서 npm install",
            )
        ]
    version = probe([node, "--version"]) or ""
    try:
        major = int(version.lstrip("v").split(".")[0])
    except ValueError:
        major = 0
    checks = []
    if major < MIN_NODE_MAJOR:
        checks.append(
            Check(
                "Node (웹)",
                WARN,
                f"{version or '버전을 읽지 못함'} ({MIN_NODE_MAJOR} 이상 권장)",
                f"Node {MIN_NODE_MAJOR} 이상으로 올리세요",
            )
        )
    else:
        checks.append(Check("Node (웹)", OK, version))
    if not (repo_root / "web" / "node_modules").is_dir():
        checks.append(
            Check("웹 패키지", WARN, "web/node_modules 없음", "web/에서 npm install (또는 npm ci)")
        )
    return checks


def check_factories(config: AdapterConfig, environ: Mapping[str, str], repo_root: Path) -> Check:
    """서버·run_fitting_once와 같은 팩토리로 어댑터를 만들어 본다(외부 서비스는 부르지 않음)."""
    real = [name for name in PORT_NAMES if config.ports[name] != FAKE]
    if not real:
        return Check("어댑터 연결", OK, "영상 단계 5개 모두 가짜 — 외부 도구 필요 없음")
    with tempfile.TemporaryDirectory(prefix="check_env_") as scratch:
        settings = AdapterSettings.from_environ(
            artifacts_dir=Path(scratch), environ=environ, repo_root=repo_root
        )
        try:
            build_ports(config, settings, fit=RuleFitCalculator())
        except AdapterConfigError as error:
            message = " ".join(line.strip() for line in str(error).splitlines())
            return Check(
                "어댑터 연결",
                MISSING,
                message,
                "위 메시지대로 고친 뒤 다시 점검하세요(docs/adapters.md '막혔을 때')",
            )
    return Check("어댑터 연결", OK, f"팩토리 {len(real)}개 연결됨: {', '.join(real)}")


# --- 모으기 --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Report:
    adapters: str  # 설정 이름이나 파일
    ports: Mapping[str, str]
    checks: tuple[Check, ...]
    repo_root: Path = REPO_ROOT  # 출력에서 저장소 안 경로를 상대 경로로 줄인다

    @property
    def missing(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if check.status == MISSING)


def run_checks(
    adapters: str | None,
    *,
    garment_id: str = DEFAULT_GARMENT_ID,
    env_file: EnvFileResult,
    environ: Mapping[str, str] | None = None,
    repo_root: Path = REPO_ROOT,
    probe: Probe = probe_version,
    which: Callable[[str], str | None] = shutil.which,
    has_module: Callable[[str], bool] = _has_module,
    version_info: Sequence[int] = sys.version_info,
    temp_dir: str | None = None,
) -> Report:
    env = os.environ if environ is None else environ
    checks: list[Check] = [
        check_python(version_info),
        check_base_packages(has_module),
        check_env_file(env_file),
        *check_paths(repo_root, temp_dir if temp_dir is not None else tempfile.gettempdir()),
    ]
    try:
        config = load_adapter_config(adapters, environ=env)
    except AdapterConfigError as error:
        checks.append(
            Check(
                "어댑터 설정",
                MISSING,
                str(error),
                f"--adapters나 {ADAPTERS_ENV}에 config/adapters/의 이름이나 yaml 경로를 주세요",
            )
        )
        return Report(
            adapters=adapters or _value(env, ADAPTERS_ENV) or "default",
            ports={},
            checks=tuple(checks),
            repo_root=repo_root,
        )
    real = [name for name in PORT_NAMES if config.ports[name] != FAKE]
    checks.append(
        Check("어댑터 설정", OK, f"{config.source} (실제: {', '.join(real) if real else '없음'})")
    )
    checks.append(check_pipeline(env))
    checks.append(check_garment(env, garment_id))

    needs = needs_for(config)
    for extra in sorted(EXTRAS):
        if f"extra:{extra}" in needs:
            checks.append(check_extra(extra, has_module))
    if NEED_BLENDER in needs:
        checks.append(check_blender(env, probe))
    if NEED_GC in needs:
        checks.append(check_gc_python(env, probe))
        checks.append(check_gc_root(env))
    if NEED_PREPARED in needs:
        checks.append(check_prepared(env, garment_id))
        dev_body = check_dev_body(env, body_is_fake=config.ports["body"] == FAKE)
        if dev_body is not None:
            checks.append(dev_body)
    if NEED_SMPL in needs:
        checks.append(check_smpl(env, repo_root))
    if NEED_FAL_KEY in needs:
        checks.append(check_fal_key(env))
    checks.extend(check_node(repo_root, probe, which))

    if any(check.status == MISSING for check in checks):
        checks.append(Check("어댑터 연결", INFO, "건너뜀 — 위의 빠진 것을 먼저 고치세요"))
    else:
        checks.append(check_factories(config, env, repo_root))
    return Report(
        adapters=config.source, ports=dict(config.ports), checks=tuple(checks), repo_root=repo_root
    )


def _short(text: str, repo_root: Path) -> str:
    """저장소 안 경로를 저장소 기준 상대 경로로 줄인다."""
    root = str(repo_root)
    for prefix in (root + "\\", root + "/", root.replace("\\", "/") + "/"):
        text = text.replace(prefix, "")
    return text


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render(report: Report) -> str:
    """사람이 읽는 표(마크다운). PR 본문에 그대로 붙일 수 있다."""
    lines = [
        f"환경 점검: {report.adapters}",
        f"Python {platform.python_version()} · {platform.system()} {platform.release()}",
    ]
    if report.ports:
        lines.append("포트: " + ", ".join(f"{name}={spec}" for name, spec in report.ports.items()))
    lines += ["", "| 항목 | 상태 | 내용 |", "|---|---|---|"]
    lines += [f"| {_cell(c.item)} | {c.status} | {_cell(c.detail)} |" for c in report.checks]
    fixes = [c for c in report.checks if c.fix and c.status in (MISSING, WARN)]
    if fixes:
        lines += ["", "고칠 것"]
        lines += [f"- [{c.status}] {c.item}: {c.fix}" for c in fixes]
    lines.append("")
    lines = [_short(line, report.repo_root) for line in lines]
    if report.missing:
        names = ", ".join(c.item for c in report.missing)
        lines.append(f"결과: 빠진 것 {len(report.missing)}개 ({names}). 고친 뒤 다시 점검하세요.")
    else:
        warns = sum(1 for c in report.checks if c.status == WARN)
        tail = f" 주의 {warns}개는 위에서 확인하세요." if warns else ""
        lines.append(f"결과: 이 설정으로 피팅을 돌리는 데 필요한 것이 모두 있습니다.{tail}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="고른 어댑터 설정으로 피팅을 돌리는 데 필요한 것만 점검한다"
    )
    parser.add_argument(
        "--adapters",
        help=f"config/adapters/의 이름이나 yaml 경로 (기본: {ADAPTERS_ENV} > default)",
    )
    parser.add_argument(
        "--garment-id",
        default=DEFAULT_GARMENT_ID,
        help=f"확인할 의류 ID (기본 {DEFAULT_GARMENT_ID}, run_fitting_once의 기준 사례)",
    )
    args = parser.parse_args(argv)
    env_file = load_env_file()
    report = run_checks(args.adapters, garment_id=args.garment_id, env_file=env_file)
    print(render(report), end="")
    return EXIT_MISSING if report.missing else EXIT_OK


if __name__ == "__main__":
    use_utf8_output()
    sys.exit(main())

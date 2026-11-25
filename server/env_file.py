"""저장소 맨 위 `.env`를 읽어 비어 있는 환경변수만 채운다 (#13, B6).

`.env.example`을 `.env`로 복사해 `FAL_KEY`·`BLENDER_PATH` 같은 값을 적어 두면, 서버
(`uvicorn server.app:app`)와 통합 실행 명령(`tools.run_fitting_once`, `tools.check_env`,
`tools.prepare_garments`)이 시작할 때 읽는다.

- 우선순위: 명령 인자 > 환경변수 > `.env` > 기본값. 이미 값이 있는 환경변수는 덮어쓰지 않는다.
  환경변수가 빈 문자열이면 없는 것으로 보고 `.env` 값을 쓴다(`AdapterSettings`와 같은 규칙).
- `.env`의 빈 값(`FAL_KEY=`)은 넣지 않는다. `.env.example`을 그대로 복사해도 아무것도 바뀌지 않는다.
- 형식: 한 줄에 `이름=값`. 빈 줄과 `#`로 시작하는 줄은 건너뛰고, 앞의 `export `와 값을 감싼 따옴표
  한 쌍은 뗀다. 따옴표가 없는 값은 ` #`(공백 + #) 뒤를 줄 끝 주석으로 보고 뗀다. 변수 치환·여러 줄
  값은 없다. 경로에 공백이 있어도 따옴표 없이 적어도 된다.
- **값은 출력·기록하지 않는다.** 돌려주는 것은 변수 이름뿐이다.
- 다른 파일을 쓰려면 환경변수 `FIT_ENV_FILE`에 경로를, 끄려면 `off`를 준다(테스트는 `off`로 돌린다:
  개발자 `.env`가 테스트 결과를 바꾸지 않게).
"""

from __future__ import annotations

import os
import re
from collections.abc import MutableMapping
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = REPO_ROOT / ".env"
ENV_FILE_ENV = "FIT_ENV_FILE"
OFF = "off"

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class EnvFileResult:
    """읽은 결과. 값은 담지 않는다."""

    path: Path | None  # 읽으려 한 파일. 꺼졌으면 None
    found: bool  # 파일이 있었는가
    loaded: tuple[str, ...] = ()  # 이번에 채운 변수 이름
    kept: tuple[str, ...] = ()  # 환경변수에 이미 값이 있어 그대로 둔 이름
    ignored_lines: tuple[int, ...] = ()  # 형식이 틀려 건너뛴 줄 번호(1부터)

    @property
    def disabled(self) -> bool:
        return self.path is None


def env_file_path(environ: MutableMapping[str, str] | None = None) -> Path | None:
    """읽을 `.env` 위치. `FIT_ENV_FILE`이 `off`면 None, 경로면 그 파일, 없으면 맨 위 `.env`."""
    env = os.environ if environ is None else environ
    raw = (env.get(ENV_FILE_ENV) or "").strip()
    if raw.lower() == OFF:
        return None
    return Path(raw) if raw else DEFAULT_ENV_FILE


def _clean_value(value: str) -> str:
    """따옴표 한 쌍을 떼고, 따옴표가 없으면 ` #` 뒤(줄 끝 주석)를 뗀다."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    if value.startswith("#"):
        return ""
    comment = value.find(" #")
    return value[:comment].rstrip() if comment >= 0 else value


def parse_env_lines(text: str) -> tuple[list[tuple[str, str]], list[int]]:
    """`.env` 내용 → ([(이름, 값)], 형식이 틀린 줄 번호)."""
    pairs: list[tuple[str, str]] = []
    bad: list[int] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        name, sep, value = line.partition("=")
        name = name.strip()
        if not sep or not _NAME.match(name):
            bad.append(number)
            continue
        pairs.append((name, _clean_value(value)))
    return pairs, bad


def load_env_file(
    path: Path | str | None = None,
    *,
    environ: MutableMapping[str, str] | None = None,
) -> EnvFileResult:
    """`.env`를 읽어 비어 있는 환경변수만 채운다.

    Args:
        path: 읽을 파일. 주지 않으면 `env_file_path()`(`FIT_ENV_FILE` > 저장소 맨 위 `.env`).
        environ: 채울 환경. 기본은 `os.environ`.
    """
    env = os.environ if environ is None else environ
    target = Path(path) if path is not None else env_file_path(env)
    if target is None:
        return EnvFileResult(path=None, found=False)
    try:
        text = target.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return EnvFileResult(path=target, found=False)
    pairs, bad = parse_env_lines(text)
    loaded: list[str] = []
    kept: list[str] = []
    for name, value in pairs:
        if not value.strip():
            continue
        if (env.get(name) or "").strip():
            kept.append(name)
            continue
        env[name] = value
        loaded.append(name)
    return EnvFileResult(
        path=target,
        found=True,
        loaded=tuple(loaded),
        kept=tuple(kept),
        ignored_lines=tuple(bad),
    )

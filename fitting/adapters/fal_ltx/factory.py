"""fal 실사화 어댑터 팩토리 (D4, docs/adapters.md "팩토리 약속").

설정 파일(`config/adapters/*.yaml`)에 이렇게 적는다.

    realize: "fitting.adapters.fal_ltx.factory:make_realizer"

실제 호출은 비용이 든다. `tools.run_fitting_once`는 이 어댑터를 `--consent`로 돌릴 때 `--yes`를
요구한다(B4). `server.wiring`을 import하므로 어댑터 본체(`realizer.py`)와 파일을 나눴다.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from server.wiring import AdapterConfigError, AdapterSettings

from .realizer import FalRealizer


class FalQueueClient:
    """`fal_client` 모듈을 감싼 대기열 클라이언트. **제출만 내부 재시도를 끄고 한 번 보낸다.**

    fal_client 1.0.3의 요청은 모두 `fal_client.client._maybe_retry_request`를 거쳐 일시 오류
    (408·409·429·502·503·504, 시간 초과·전송 오류) 때 최대 `MAX_ATTEMPTS`(10)번 다시 보낸다.
    제출을 이렇게 다시 보내면 이미 접수된 요청이 또 접수돼 생성 비용이 두 번 나갈 수 있다.
    그래서 제출하는 동안만
    `fal_client.client.MAX_ATTEMPTS`를 1로 두고 끝나면 되돌린다(값은 호출할 때마다 읽힌다).

    - 업로드·상태 조회·결과는 같은 요청을 다시 보내도 비용이 들지 않아 내부 재시도를 그대로 쓴다.
    - 모듈 전역 값을 잠깐 바꾸므로 잠금으로 제출끼리 겹치지 않게 한다. 서버는 영상 작업을 한 번에
      하나만 돌린다(`FIT_MAX_WORKERS` 기본 1, B3).
    - fal_client 내부 구조가 바뀌어 `client.MAX_ATTEMPTS`가 없으면 그냥 부르고 `retry_guard`가
      False가 된다(지표·기록에 남는다). `pyproject.toml`은 fal-client를 2 미만으로 묶는다.
    """

    _submit_lock = threading.Lock()

    def __init__(self, module: Any) -> None:
        self._module = module
        inner = getattr(module, "client", None)
        self._inner = inner if hasattr(inner, "MAX_ATTEMPTS") else None

    @property
    def retry_guard(self) -> bool:
        """제출할 때 내부 재시도를 끌 수 있는지."""
        return self._inner is not None

    def upload_file(self, path: Path) -> str:
        return self._module.upload_file(path)

    def submit(self, application: str, arguments: dict[str, Any]) -> Any:
        if self._inner is None:
            return self._module.submit(application, arguments)
        with self._submit_lock:
            saved = self._inner.MAX_ATTEMPTS
            self._inner.MAX_ATTEMPTS = 1
            try:
                return self._module.submit(application, arguments)
            finally:
                self._inner.MAX_ATTEMPTS = saved

    def status(self, application: str, request_id: str, *, with_logs: bool = False) -> Any:
        return self._module.status(application, request_id, with_logs=with_logs)

    def result(self, application: str, request_id: str) -> dict[str, Any]:
        return self._module.result(application, request_id)


def make_realizer(settings: AdapterSettings) -> FalRealizer:
    """`fal_client`로 fal을 부르는 실사화 어댑터를 만든다.

    키 값은 `fal_client`가 환경변수 `FAL_KEY`에서 직접 읽는다(설정 객체에는 값이 없다).

    Raises:
        AdapterConfigError: FAL_KEY가 없거나 선택 의존성 `realize`가 설치되지 않았다.
    """
    if not settings.fal_key_present:
        raise AdapterConfigError(
            "FAL_KEY가 비어 있습니다. 환경변수나 .env에 fal API 키를 넣으세요"
            "(값은 커밋하지 않습니다)"
        )
    try:
        import fal_client
    except ImportError as error:
        raise AdapterConfigError(
            'fal_client가 없습니다. pip install -e ".[realize]"로 설치하세요'
        ) from error
    version = getattr(fal_client, "__version__", None) or "unknown"
    queue = FalQueueClient(fal_client)
    return FalRealizer(
        client=queue,
        artifacts_dir=settings.artifacts_dir,
        work_dir=settings.work_dir,
        client_version=f"{version} (submit_retry_guard={'on' if queue.retry_guard else 'off'})",
    )

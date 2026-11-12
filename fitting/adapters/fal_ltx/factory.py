"""fal 실사화 어댑터 팩토리 (D4, docs/adapters.md "팩토리 약속").

설정 파일(`config/adapters/*.yaml`)에 이렇게 적는다.

    realize: "fitting.adapters.fal_ltx.factory:make_realizer"

실제 호출은 비용이 든다. `tools.run_fitting_once`는 이 어댑터를 `--consent`로 돌릴 때 `--yes`를
요구한다(B4). `server.wiring`을 import하므로 어댑터 본체(`realizer.py`)와 파일을 나눴다.
"""

from __future__ import annotations

from server.wiring import AdapterConfigError, AdapterSettings

from .realizer import FalRealizer


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
    return FalRealizer(
        client=fal_client,
        artifacts_dir=settings.artifacts_dir,
        work_dir=settings.work_dir,
        client_version=str(version),
    )

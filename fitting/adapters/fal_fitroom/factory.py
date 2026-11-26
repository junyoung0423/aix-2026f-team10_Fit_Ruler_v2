"""피팅룸 실사화 어댑터 팩토리 (docs/adapters.md "팩토리 약속").

    realize: "fitting.adapters.fal_fitroom.factory:make_fitroom_realizer"

실제 호출은 비용이 든다(작업 1건 $0.70, 자세 사진을 끄면 -$0.15, 회전을 표준판으로 하면 +$0.40).
`tools.run_fitting_once`는
실사화 포트가 가짜가 아니면 `--consent`로 돌릴 때 `--yes`를 요구한다. 제출할 때 fal_client 내부
재시도를 끄는 클라이언트는 D4 팩토리의 `FalQueueClient`를 그대로 쓴다.
"""

from __future__ import annotations

from server.wiring import AdapterConfigError, AdapterSettings

from .realizer import FitRoomRealizer


def make_fitroom_realizer(settings: AdapterSettings) -> FitRoomRealizer:
    """Raises: AdapterConfigError — FAL_KEY가 없거나 선택 의존성 `realize`가 설치되지 않았다."""
    if not settings.fal_key_present:
        raise AdapterConfigError(
            "FAL_KEY가 비어 있습니다. 환경변수나 .env에 fal API 키를 넣으세요(값은 커밋하지 "
            "않습니다)"
        )
    try:
        import fal_client

        from fitting.adapters.fal_ltx.factory import FalQueueClient
    except ImportError as error:
        raise AdapterConfigError(
            'fal_client나 OpenCV가 없습니다. pip install -e ".[realize]"로 설치하세요'
        ) from error
    version = getattr(fal_client, "__version__", None) or "unknown"
    queue = FalQueueClient(fal_client)
    return FitRoomRealizer(
        client=queue,
        artifacts_dir=settings.artifacts_dir,
        work_dir=settings.work_dir,
        client_version=f"{version} (submit_retry_guard={'on' if queue.retry_guard else 'off'})",
    )

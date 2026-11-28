"""피팅룸 실사화 어댑터 팩토리 (docs/adapters.md "팩토리 약속").

    realize: "fitting.adapters.fal_fitroom.factory:make_fitroom_realizer"

실제 호출은 비용이 든다(작업 1건 $1.10, 자세 사진을 끄면 -$0.15, 회전을 표준판으로 하면 +$0.80).
사진 핏 확인에서 떨어져 사진을 다시 만들면 1장당 +$0.15다(기본 최대 3장, +$0.30까지). 핏 확인 기준은
검증 어댑터와 같은 설정 파일(`config/validate/visual_check_v1.json`, `fitroom_check_v1.json`)이다.
`tools.run_fitting_once`는 실사화 포트가 가짜가 아니면 `--consent`로 돌릴 때 `--yes`를 요구한다.
제출할 때 fal_client 내부 재시도를 끄는 클라이언트는 D4 팩토리의 `FalQueueClient`를 그대로 쓴다.

회전 영상 맨 위 자르기는 Blender로 H.264를 다시 쓰므로 `settings.blender_path`(BLENDER_PATH, 없으면
BLENDER_EXE)를 넘긴다. 없으면 실사화가 fal을 부르기 전에 멈춘다(착장·렌더도 Blender가 있어야 한다).
"""

from __future__ import annotations

from server.wiring import AdapterConfigError, AdapterSettings

from .realizer import FitRoomRealizer, PhotoCheck


def make_fitroom_realizer(settings: AdapterSettings) -> FitRoomRealizer:
    """Raises: AdapterConfigError — FAL_KEY가 없거나, 선택 의존성 `realize`가 설치되지 않았거나,
    사진 핏 확인 설정을 읽지 못했다."""
    if not settings.fal_key_present:
        raise AdapterConfigError(
            "FAL_KEY가 비어 있습니다. 환경변수나 .env에 fal API 키를 넣으세요(값은 커밋하지 "
            "않습니다)"
        )
    try:
        import fal_client

        from fitting.adapters.fal_ltx.factory import FalQueueClient
        from fitting.adapters.media.fitroom_measure import (
            DEFAULT_FITROOM_CONFIG,
            load_fitroom_config,
        )
        from fitting.adapters.media.visual_measure import DEFAULT_CHECK_CONFIG, load_check_config
    except ImportError as error:
        raise AdapterConfigError(
            'fal_client나 OpenCV가 없습니다. pip install -e ".[realize]"로 설치하세요'
        ) from error
    try:
        check = PhotoCheck(
            config=load_check_config(DEFAULT_CHECK_CONFIG),
            align=load_fitroom_config(DEFAULT_FITROOM_CONFIG).align,
        )
    except (OSError, ValueError) as error:
        raise AdapterConfigError(f"사진 핏 확인 설정을 읽지 못했습니다: {error}") from error
    version = getattr(fal_client, "__version__", None) or "unknown"
    queue = FalQueueClient(fal_client)
    return FitRoomRealizer(
        client=queue,
        artifacts_dir=settings.artifacts_dir,
        work_dir=settings.work_dir,
        client_version=f"{version} (submit_retry_guard={'on' if queue.retry_guard else 'off'})",
        blender_path=settings.blender_path,
        photo_check=check,
    )

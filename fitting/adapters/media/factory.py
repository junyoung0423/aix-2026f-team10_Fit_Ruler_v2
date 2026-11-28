"""B4 팩토리: 실사 영상 검증 어댑터(D5·D6). server를 import하므로 어댑터 본체와 따로 둔다."""

from __future__ import annotations

from server.wiring import AdapterConfigError, AdapterSettings


def make_validator(settings: AdapterSettings):
    """명세서 7장 색·실루엣 검증 어댑터를 만든다. 기준은 `config/validate/visual_check_v1.json`,
    피팅룸 실사화 결과 검사는 `config/validate/fitroom_check_v1.json`.

    Raises:
        AdapterConfigError: 선택 의존성(OpenCV)이 없거나 검증 설정을 읽지 못했다.
    """
    try:
        from .fitroom_measure import DEFAULT_FITROOM_CONFIG, load_fitroom_config
        from .validator import VisualValidator
        from .visual_measure import DEFAULT_CHECK_CONFIG, load_check_config
    except ImportError as error:
        raise AdapterConfigError(
            'OpenCV(cv2)가 없습니다. pip install -e ".[realize]"로 설치하세요'
        ) from error
    try:
        config = load_check_config(DEFAULT_CHECK_CONFIG)
        fitroom = load_fitroom_config(DEFAULT_FITROOM_CONFIG)
    except (OSError, ValueError) as error:
        raise AdapterConfigError(f"검증 설정을 읽지 못했습니다: {error}") from error
    return VisualValidator(
        artifacts_dir=settings.artifacts_dir,
        work_dir=settings.work_dir,
        config=config,
        fitroom=fitroom,
    )

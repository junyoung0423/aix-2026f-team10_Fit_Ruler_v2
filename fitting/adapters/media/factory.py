"""B4 팩토리: D5 검증 어댑터(프레임 부분). server를 import하므로 어댑터 본체와 따로 둔다."""

from __future__ import annotations

from server.wiring import AdapterConfigError, AdapterSettings


def make_validator(settings: AdapterSettings):
    """대표 프레임·대응 렌더 프레임을 저장하는 검증 어댑터를 만든다(판정은 D6 전이라 UNVERIFIABLE).

    Raises:
        AdapterConfigError: 선택 의존성(OpenCV)이 설치되지 않았다.
    """
    try:
        from .validator import FrameValidator
    except ImportError as error:
        raise AdapterConfigError(
            'OpenCV(cv2)가 없습니다. pip install -e ".[realize]"로 설치하세요'
        ) from error
    return FrameValidator(artifacts_dir=settings.artifacts_dir, work_dir=settings.work_dir)

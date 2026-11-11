"""가짜 영상 단계에 지연을 넣는다. 화면과 수동 확인에서 진행 상태를 눈으로 보기 위한 것이다.

가짜 어댑터는 바로 끝나서 몸 → 착장 → 렌더 → 실사화 → 검증이 넘어가는 모습을 볼 수 없다.
`with_stage_delay`는 영상 경로 다섯 포트의 호출 앞에서 정해진 시간만 쉬게 한다. 핏 계산기는
그대로 둔다. 기존 가짜 어댑터의 동작과 "실제 파일을 만들지 않는다"는 약속은 바뀌지 않는다.
"""

import math
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from fitting.services.fitting import FittingPorts


class _Delayed:
    """포트를 감싸서 메서드를 부르기 전에 쉰다. 메서드가 아닌 속성(예: `calls`)은 그대로 보인다."""

    def __init__(self, port: Any, seconds: float, sleep: Callable[[float], None]) -> None:
        self._port = port
        self._seconds = seconds
        self._sleep = sleep

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._port, name)
        if not callable(attribute):
            return attribute

        def delayed(*args: Any, **kwargs: Any) -> Any:
            self._sleep(self._seconds)
            return attribute(*args, **kwargs)

        return delayed


def with_stage_delay(
    ports: FittingPorts,
    seconds: float,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> FittingPorts:
    """영상 경로 포트(몸·착장·렌더·실사화·검증)가 불릴 때마다 `seconds`초 쉬는 포트 묶음.

    `seconds`가 0이면 원래 포트 묶음을 그대로 돌려준다. `sleep`은 테스트가 실제로 기다리지 않게
    바꿔 끼우는 용도다.

    Raises:
        ValueError: `seconds`가 음수이거나 유한한 숫자가 아니다.
    """
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError(f"지연 시간은 0 이상의 유한한 숫자여야 합니다: {seconds}")
    if seconds == 0:
        return ports
    return replace(
        ports,
        body=_Delayed(ports.body, seconds, sleep),
        drape=_Delayed(ports.drape, seconds, sleep),
        render=_Delayed(ports.render, seconds, sleep),
        realize=_Delayed(ports.realize, seconds, sleep),
        validate=_Delayed(ports.validate, seconds, sleep),
    )

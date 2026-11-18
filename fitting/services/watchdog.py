"""포트 호출의 감시 시간 (B5, #12).

어댑터는 `PortRequest.timeout_seconds` 안에 스스로 끝내야 한다. 그런데 어댑터가 멈추면 호출이
돌아오지 않고 작업이 영원히 진행 중으로 남으며, 동시 실행이 1개라 뒤 작업도 전부 멈춘다. 그래서
호출을 별도 데몬 스레드에서 하고, 시간 제한 + 여유가 지나면 서비스가 그 호출을 포기한다.

**한계:** 파이썬은 스레드를 강제로 끝낼 수 없다. 포기한 호출은 끝까지 돌고, 늦게 돌아온 결과는
버리고 로그만 남긴다. Blender 같은 외부 프로그램을 끝내는 일은 어댑터 책임이다.
"""

import logging
import threading
from collections.abc import Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# (끝났다는 신호, 기다릴 초) → 신호가 왔으면 True. 테스트가 실제 시간을 기다리지 않게 바꿔 끼운다.
Wait = Callable[[threading.Event, float], bool]


def real_wait(done: threading.Event, seconds: float) -> bool:
    return done.wait(seconds)


class WatchdogExpired(Exception):
    """감시 시간 안에 호출이 돌아오지 않았다."""

    def __init__(self, label: str, waited_s: float) -> None:
        super().__init__(f"{label}: {waited_s:g}초 안에 돌아오지 않았습니다")
        self.label = label
        self.waited_s = waited_s


def call_with_watchdog(
    fn: Callable[[], T], *, limit_s: float, label: str, wait: Wait = real_wait
) -> T:
    """`fn()`을 데몬 스레드에서 부르고 `limit_s`초까지 기다린다.

    안에 끝나면 결과를 돌려주거나 `fn`이 던진 예외를 그대로 던진다.

    Raises:
        WatchdogExpired: `limit_s`초 안에 끝나지 않았다. 호출은 스레드에서 계속 돌 수 있다.
    """
    done = threading.Event()
    box: dict[str, Any] = {}
    abandoned = threading.Event()

    def target() -> None:
        try:
            box["value"] = fn()
        except Exception as error:  # 호출한 쪽에서 그대로 다시 던진다
            box["error"] = error
        finally:
            done.set()
            if abandoned.is_set():
                outcome = "예외" if "error" in box else "결과"
                logger.warning("%s: 포기한 호출이 늦게 끝나 %s를 버렸습니다", label, outcome)

    thread = threading.Thread(target=target, name=f"watchdog-{label}", daemon=True)
    thread.start()
    if not wait(done, limit_s):
        abandoned.set()
        raise WatchdogExpired(label, limit_s)
    if "error" in box:
        raise box["error"]
    return box["value"]

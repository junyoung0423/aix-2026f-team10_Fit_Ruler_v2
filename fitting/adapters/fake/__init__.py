"""가짜 어댑터 (#1). 외부 도구 없이 피팅 전체 흐름을 돌리기 위한 테스트·CI용 구현.

실제 어댑터가 생기기 전에는 각자 이 가짜를 써서 자기 단계 앞뒤를 확인한다.
`fail_with`로 실패를, `no_artifacts=True`로 산출물 ID가 빠진 잘못된 응답을 흉내 낼 수 있다.
"""

from fitting.adapters.fake.adapters import (
    FakeBodyBuilder,
    FakeDraper,
    FakeFitCalculator,
    FakeRealizer,
    FakeRenderer,
    FakeValidator,
    fake_ports,
)

__all__ = [
    "FakeBodyBuilder",
    "FakeDraper",
    "FakeFitCalculator",
    "FakeRealizer",
    "FakeRenderer",
    "FakeValidator",
    "fake_ports",
]

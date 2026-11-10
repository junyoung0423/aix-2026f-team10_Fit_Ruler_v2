"""CI가 빈 저장소에서도 도는지 확인한다. 실제 테스트가 생기면 지운다."""

import fitting


def test_package_imports() -> None:
    assert fitting.__version__

"""명령 출력 인코딩 (#13, B6). 모든 `python -m tools.<이름>`이 시작할 때 부른다.

한국어 Windows에서 출력을 파일·파이프로 돌리면 Python이 표준 출력을 cp949로 연다. 그러면
"—"·"×" 같은 cp949에 없는 문자를 찍을 때 `UnicodeEncodeError`로 멈추고, 한국어도 UTF-8로 읽는
쪽(테스트의 하위 프로세스, 다른 도구)에서 깨진다. 그래서 명령으로 실행할 때만 표준 출력·표준 오류를
UTF-8로 바꾼다.

- 터미널에 바로 찍을 때는 이미 UTF-8이라 바꾸지 않는다.
- `main()`을 함수로 부르는 테스트에는 영향이 없다(`if __name__ == "__main__":`에서만 부른다).
- `PYTHONUTF8=1`을 켠 것과 같은 출력이 된다. 다른 입출력(파일 열기 등)은 바꾸지 않는다.

이 파일은 GarmentCode용 Python 3.9(`tools.build_sample_catalog`)에서도 불린다. 표준 라이브러리만
쓰고 3.10 문법을 쓰지 않는다.
"""

import sys


def _is_utf8(encoding: object) -> bool:
    name = str(encoding or "").lower().replace("-", "").replace("_", "")
    return name in ("utf8", "utf8sig")


def use_utf8_output(streams=None) -> None:
    """표준 출력·표준 오류를 UTF-8로 바꾼다. 이미 UTF-8이거나 바꿀 수 없는 스트림은 그대로 둔다."""
    for stream in (sys.stdout, sys.stderr) if streams is None else streams:
        if stream is None or _is_utf8(getattr(stream, "encoding", None)):
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError):  # 이미 닫혔거나 바꿀 수 없는 스트림
            continue

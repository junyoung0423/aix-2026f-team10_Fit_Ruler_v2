"""가짜 모드의 자리표시 이미지 (#12). 화면에서 산출물 주소가 실제로 열리는지 보기 위한 것이다.

가짜 어댑터는 산출물 ID만 만들고 파일은 만들지 않는다. `FIT_FAKE_PLACEHOLDERS=1`이면 이미지
산출물(3D 미리보기, 대표 프레임, 대응 렌더 프레임)의 ID가 상태에 나타날 때 산출물 폴더에 단색
384×512 PNG를 `{artifact_id}.png`로 쓴다. 표준 라이브러리(`zlib`·`struct`)만 쓰고,
영상(mp4)은 만들지 않는다.

가짜 어댑터 파일(`fitting/adapters/fake/`)은 바꾸지 않고, 서버가 포트를 감싸서 쓴다. 파일은 결과가
돌아가기 전에 쓰므로, 상태에 ID가 보일 때는 이미 열 수 있다.
"""

import logging
import os
import struct
import zlib
from dataclasses import replace
from pathlib import Path
from typing import Any

from fitting.ports import DRAPE_OUTPUTS, VALIDATE_OUTPUTS, output_artifact
from fitting.services.fitting import FittingPorts
from server.artifacts import is_valid_artifact_id

logger = logging.getLogger(__name__)

PLACEHOLDER_WIDTH = 384
PLACEHOLDER_HEIGHT = 512

# (산출물 역할 목록, 역할 이름, 색). 역할마다 색이 달라야 화면에서 서로 구분된다.
_DRAPE_PREVIEW = (DRAPE_OUTPUTS, "drape_preview", (143, 164, 191))
_FRAME = (VALIDATE_OUTPUTS, "frame", (214, 176, 140))
_RENDER_FRAME = (VALIDATE_OUTPUTS, "render_frame", (140, 190, 176))


def solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """단색 RGB PNG 파일의 바이트."""
    row = b"\x00" + bytes(rgb) * width  # 앞의 0은 필터 없음
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8비트 RGB

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(row * height, 9))
        + chunk(b"IEND", b"")
    )


class _WritesPlaceholders:
    """포트를 감싸, 결과에 이미지 산출물 ID가 있으면 그 ID로 자리표시 PNG를 쓴다."""

    def __init__(self, port: Any, directory: Path, roles: list[tuple]) -> None:
        self._port = port
        self._directory = directory
        self._roles = roles

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._port, name)
        if not callable(attribute):
            return attribute

        def call(*args: Any, **kwargs: Any) -> Any:
            result = attribute(*args, **kwargs)
            for outputs, role, color in self._roles:
                artifact_id = output_artifact(result.output_artifact_ids, outputs, role)
                if artifact_id is not None:
                    self._write(artifact_id, color)
            return result

        return call

    def _write(self, artifact_id: str, color: tuple[int, int, int]) -> None:
        if not is_valid_artifact_id(artifact_id):
            logger.warning("자리표시 이미지를 쓰지 않습니다(ID 형식): %s", artifact_id)
            return
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            final = self._directory / f"{artifact_id}.png"
            # 점으로 시작하는 임시 이름은 산출물 찾기(`{id}.*`)에 걸리지 않는다.
            temporary = self._directory / f".{artifact_id}.png.tmp"
            temporary.write_bytes(solid_png(PLACEHOLDER_WIDTH, PLACEHOLDER_HEIGHT, color))
            os.replace(temporary, final)
        except OSError:
            # 자리표시는 보여 주기용이다. 못 써도 피팅은 멈추지 않는다(그 주소는 404가 된다).
            logger.exception("자리표시 이미지를 쓰지 못했습니다: %s", artifact_id)


def with_placeholders(ports: FittingPorts, directory: Path | str) -> FittingPorts:
    """착장(3D 미리보기)과 검증(대표 프레임·대응 렌더 프레임) 포트가 자리표시 PNG를 쓰게 한다."""
    directory = Path(directory)
    return replace(
        ports,
        drape=_WritesPlaceholders(ports.drape, directory, [_DRAPE_PREVIEW]),
        validate=_WritesPlaceholders(ports.validate, directory, [_FRAME, _RENDER_FRAME]),
    )

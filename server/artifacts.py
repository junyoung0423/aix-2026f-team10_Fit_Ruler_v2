"""산출물 파일 찾기 (#12). `GET /api/artifacts/{artifact_id}`가 쓴다.

산출물 파일은 산출물 폴더 안에 `{artifact_id}.{확장자}`라는 이름으로 있다. 실제 어댑터(C4·C5·D4·D5,
연결은 B4)가 이 폴더에 이 이름으로 쓴다. 같은 ID로 확장자가 다른 파일이 여러 개면 파일 이름 순서로
첫 번째를 준다.

ID는 영문자·숫자·`_`·`-` 1~128자만 받는다. `..`나 `/`가 든 ID는 파일을 찾아보지도 않고 없는 것으로
본다. 찾은 파일은 실제 경로(심볼릭 링크를 따라간 뒤)가 산출물 폴더 안인지도 확인한다.
"""

import mimetypes
import re
from pathlib import Path

# 폴더 이름: 인자 `artifacts_dir` > 환경변수 `FIT_ARTIFACTS_DIR` > `artifacts/files/`(.gitignore)
DEFAULT_ARTIFACTS_SUBDIR = Path("artifacts") / "files"

_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}")

# 확장자 → Content-Type. 운영체제 설정에 따라 달라지지 않게 자주 쓰는 것은 직접 적는다.
_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".json": "application/json",
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
}


def is_valid_artifact_id(artifact_id: str) -> bool:
    """`^[A-Za-z0-9_-]{1,128}$`. 끝의 줄바꿈도 받지 않는다."""
    return _ID_PATTERN.fullmatch(artifact_id) is not None


def media_type(path: Path) -> str:
    """파일 확장자로 Content-Type을 정한다. 모르면 `application/octet-stream`."""
    suffix = path.suffix.lower()
    if suffix in _MEDIA_TYPES:
        return _MEDIA_TYPES[suffix]
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


class ArtifactDirectory:
    """산출물 폴더. 요청마다 폴더를 다시 읽는다(파일을 어댑터가 나중에 써 넣는다)."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def find(self, artifact_id: str) -> Path | None:
        """산출물 ID의 파일. 형식이 틀렸거나, 없거나, 폴더 밖을 가리키면 None."""
        if not is_valid_artifact_id(artifact_id) or not self.path.is_dir():
            return None
        root = self.path.resolve()
        for candidate in sorted(self.path.glob(f"{artifact_id}.*")):
            resolved = candidate.resolve()
            if resolved.is_file() and resolved.is_relative_to(root):
                return resolved
        return None

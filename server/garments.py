"""등록 의류 디렉터리 읽기 (#2).

디렉터리의 `*.json` 한 파일이 의류 한 벌(`GarmentVariant`)이다. C1(AI Hub 변환)도 같은
디렉터리에 같은 형식으로 쓴다(docs/api/README.md "의류 등록 형식").

형식에 맞지 않는 파일과 이미 나온 ID의 파일은 건너뛰고 경고를 남긴다. 한 파일이 잘못되어도
나머지 의류는 계속 쓸 수 있게 하기 위해서다.
"""

import logging
from pathlib import Path

from pydantic import ValidationError

from fitting.domain import GarmentStatus, GarmentVariant

logger = logging.getLogger(__name__)


class GarmentDirectory:
    """요청마다 디렉터리를 다시 읽는다. 등록 의류는 수십 벌이라 캐시하지 않는다."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def all(self) -> list[GarmentVariant]:
        """읽을 수 있는 모든 의류(상태 무관). 파일 이름 순서다."""
        garments: dict[str, GarmentVariant] = {}
        if not self.path.is_dir():
            logger.warning("의류 디렉터리가 없습니다: %s", self.path)
            return []
        for file in sorted(self.path.glob("*.json")):
            try:
                garment = GarmentVariant.model_validate_json(file.read_bytes())
            except (OSError, ValidationError) as error:
                logger.warning("의류 파일을 건너뜁니다(형식 오류): %s: %s", file.name, error)
                continue
            if garment.id in garments:
                logger.warning("의류 파일을 건너뜁니다(ID 중복 %s): %s", garment.id, file.name)
                continue
            garments[garment.id] = garment
        return list(garments.values())

    def ready(self) -> list[GarmentVariant]:
        """일반 목록에 보일 READY 의류만 (명세서 S04)."""
        return [g for g in self.all() if g.status is GarmentStatus.READY]

    def get(self, garment_id: str) -> GarmentVariant | None:
        """ID로 찾는다. READY가 아니어도 돌려주고, 계산 가능 여부는 계산기가 판단한다."""
        return next((g for g in self.all() if g.id == garment_id), None)

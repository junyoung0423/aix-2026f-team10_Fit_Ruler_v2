"""착장 점검용 메시 기하(D4): OBJ 읽기, 닫힘 검사, 몸 표면 부호 거리, 점검 실행·기록.

판정 규칙은 `fitting/verify/drape_check.py`(로직)에 있고,
이 패키지는 메시를 계산해 그 규칙에 넘긴다.
"""

from .check import (
    RECORD_NAME,
    MeshInput,
    check_drape,
    check_drape_files,
    load_c2_run,
    load_obj,
    load_thresholds,
    write_record,
)
from .geometry import (
    closest_points_on_triangles,
    face_centers,
    is_closed,
    read_obj,
    signed_distance,
    triangle_areas,
    vertex_normals,
)

__all__ = [
    "RECORD_NAME",
    "MeshInput",
    "check_drape",
    "check_drape_files",
    "closest_points_on_triangles",
    "face_centers",
    "is_closed",
    "load_c2_run",
    "load_obj",
    "load_thresholds",
    "read_obj",
    "signed_distance",
    "triangle_areas",
    "vertex_normals",
    "write_record",
]

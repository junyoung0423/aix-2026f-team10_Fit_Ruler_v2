"""AI Hub 의류 통합 데이터(dataSetSn 71501) 변환 표 (#3).

출처 값 -> 우리 계약 값의 대응표다. 근거가 데이터 설명서가 아니라 값의 범위나 우리 판단인 것은
`[제안]`으로 표시한다(docs/spikes/aihub-71501.md, docs/decisions.md).
"""

# 반팔 라운드넥 티셔츠를 골라내는 범주 값 (데이터 구조표 4-1, 4-15, 4-16)
TSHIRT_TYPE = "02top_02t-shirt"
SHORT_SLEEVES = "short sleeves"
ROUND_NECK = "round neck"

# 색 키 -> 화면 색 견본 대표값. 출처에 hex가 없어서 정한 [제안] 값이다.
# 데이터 구조표 4-7의 색상 목록(Black ~ Beige)을 영어 소문자 키로 바꾼다. Mixed와 여러 색
# ("Black,White")은 무지로 볼 수 없어 받지 않는다([제안]).
# navy·black·white·gray는 화면(web/src/labels.ts)의 임시 표와 같은 값이다.
COLOR_HEX: dict[str, str] = {
    "black": "#222326",
    "white": "#f4f4f2",
    "gray": "#8d9096",
    "red": "#c0392b",
    "orange": "#e67e22",
    "pink": "#f2a7bb",
    "yellow": "#f1d24a",
    "brown": "#7b5539",
    "green": "#3f7d4e",
    "blue": "#2f5fa7",
    "purple": "#6c4a9e",
    "beige": "#d9c8a9",
    "navy": "#24324f",  # AI Hub 색상 목록에는 없다. 시연용 의류와 다른 출처를 위해 둔다
}

# 출처 색 이름 -> 색 키
AIHUB_COLOR: dict[str, str] = {
    name: name.lower()
    for name in (
        "Black",
        "White",
        "Gray",
        "Red",
        "Orange",
        "Pink",
        "Yellow",
        "Brown",
        "Green",
        "Blue",
        "Purple",
        "Beige",
    )
}

# 출처 섬유 이름(데이터 구조표 4-3) -> (섬유 키, 프롬프트용 영어)
AIHUB_FIBER: dict[str, tuple[str, str]] = {
    "Cotton": ("cotton", "cotton"),
    "Hemp": ("hemp", "hemp"),
    "cellulose fiber Others": ("cellulose_other", "cellulose fiber"),
    "Silk": ("silk", "silk"),
    "Wool": ("wool", "wool"),
    "protein fiber Others": ("protein_other", "protein fiber"),
    "Viscos rayon": ("viscose_rayon", "viscose rayon"),
    "regenerated fiber Others": ("regenerated_other", "regenerated fiber"),
    "Polyester": ("polyester", "polyester"),
    "Nylon": ("nylon", "nylon"),
    "Polyurethane": ("polyurethane", "polyurethane"),
    "synthetic fiber Others": ("synthetic_other", "synthetic fiber"),
}

# 신축성 값(데이터 구조표 4-4). 값은 그대로 옮기고, 목록에 없는 값만 거절한다.
AIHUB_STRETCH = frozenset({"none at all", "none", "contain little", "contain", "contain a lot"})

# 치수 필드(`metadata.top.*`) -> 받아들이는 범위(cm, 경계 포함). [제안]
# 데이터 설명서에 단위·측정 방법이 없어(구조표는 0~999) 값의 분포로 정했다. Validation 반팔
# 라운드넥 3,223벌 중 54벌(1.7%)이 범위 밖이다. 범위 밖은 단위 오류(mm 등)나 입력 오류로 보고
# 이유와 함께 거절한다. 0은 "값 없음"으로 보고 null로 둔다.
MEASUREMENT_RANGES_CM: dict[str, tuple[float, float]] = {
    "chest_size": (60.0, 200.0),  # 상의가슴둘레 (둘레, 단면 아님)
    "waist_size": (50.0, 200.0),  # 상의허리둘레
    "shoulder_width": (25.0, 80.0),  # 상의어깨너비
    "sleeve_length": (5.0, 45.0),  # 상의소매길이 (반팔)
    "front_length": (35.0, 100.0),  # 상의앞길이 (기준점 미상, 계약에는 넣지 않고 notes에만)
}

# 의류군이 맞을 때 쓰는 원단 프리셋. 천 물성 매핑은 C4에서 한다.
DEFAULT_FABRIC_PRESET = "cotton_jersey_v0"

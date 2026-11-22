"""렌더 색 계산 (C5). 외부 의존성 없는 순수 파이썬이라 Blender 안과 서버 양쪽에서 쓴다.

- `hex_to_srgb`, `srgb_to_linear`: 의류 `color_hex`(sRGB) → Principled BSDF 기본 색(선형)
- `srgb_to_lab`, `ciede2000`: 렌더한 옷 색과 `color_hex` 비교(기록용, 판정하지 않음)

식은 IEC 61966-2-1(sRGB, D65)과 Sharma·Wu·Dalal(2005)의 CIEDE2000이다.
"""

from __future__ import annotations

import math

HEX_DIGITS = "0123456789abcdefABCDEF"


def hex_to_srgb(value: str) -> tuple[float, float, float]:
    """`#rrggbb` → sRGB 0~1. 형식이 틀리면 ValueError."""
    text = value.strip()
    if len(text) != 7 or text[0] != "#" or any(c not in HEX_DIGITS for c in text[1:]):
        raise ValueError(f"색 형식이 #rrggbb가 아닙니다: {value!r}")
    return tuple(int(text[i : i + 2], 16) / 255.0 for i in (1, 3, 5))  # type: ignore[return-value]


def srgb_to_hex(rgb: tuple[float, float, float]) -> str:
    """sRGB 0~1 → `#rrggbb`(반올림, 0~255로 자름)."""
    return "#" + "".join(f"{min(255, max(0, round(c * 255.0))):02x}" for c in rgb)


def srgb_to_linear(c: float) -> float:
    """sRGB 전달 함수의 역(채널 하나, 0~1)."""
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def linear_to_srgb(c: float) -> float:
    c = max(0.0, c)
    return c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def hex_to_linear(value: str) -> tuple[float, float, float]:
    r, g, b = hex_to_srgb(value)
    return srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b)


def srgb_to_lab(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    """sRGB 0~1 → CIE L*a*b*(D65)."""
    r, g, b = (srgb_to_linear(c) for c in rgb)
    x = 0.4124564 * r + 0.3575761 * g + 0.1804375 * b
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = 0.0193339 * r + 0.1191920 * g + 0.9503041 * b
    xn, yn, zn = 0.95047, 1.0, 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > (6 / 29) ** 3 else t / (3 * (6 / 29) ** 2) + 4 / 29

    fx, fy, fz = f(x / xn), f(y / yn), f(z / zn)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def ciede2000(lab1: tuple[float, float, float], lab2: tuple[float, float, float]) -> float:
    """CIEDE2000 색 차이(kL = kC = kH = 1)."""
    l1, a1, b1 = lab1
    l2, a2, b2 = lab2
    c1, c2 = math.hypot(a1, b1), math.hypot(a2, b2)
    cbar = (c1 + c2) / 2
    g = 0.5 * (1 - math.sqrt(cbar**7 / (cbar**7 + 25**7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360 if c1p else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360 if c2p else 0.0
    dlp, dcp = l2 - l1, c2p - c1p
    if c1p * c2p == 0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    elif h2p - h1p > 180:
        dhp = h2p - h1p - 360
    else:
        dhp = h2p - h1p + 360
    dhp_big = 2 * math.sqrt(c1p * c2p) * math.sin(math.radians(dhp / 2))
    lbar, cbarp = (l1 + l2) / 2, (c1p + c2p) / 2
    if c1p * c2p == 0:
        hbarp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbarp = (h1p + h2p) / 2
    elif h1p + h2p < 360:
        hbarp = (h1p + h2p + 360) / 2
    else:
        hbarp = (h1p + h2p - 360) / 2
    t = (
        1
        - 0.17 * math.cos(math.radians(hbarp - 30))
        + 0.24 * math.cos(math.radians(2 * hbarp))
        + 0.32 * math.cos(math.radians(3 * hbarp + 6))
        - 0.20 * math.cos(math.radians(4 * hbarp - 63))
    )
    dtheta = 30 * math.exp(-(((hbarp - 275) / 25) ** 2))
    rc = 2 * math.sqrt(cbarp**7 / (cbarp**7 + 25**7))
    sl = 1 + 0.015 * (lbar - 50) ** 2 / math.sqrt(20 + (lbar - 50) ** 2)
    sc = 1 + 0.045 * cbarp
    sh = 1 + 0.015 * cbarp * t
    rt = -math.sin(math.radians(2 * dtheta)) * rc
    return math.sqrt(
        (dlp / sl) ** 2 + (dcp / sc) ** 2 + (dhp_big / sh) ** 2 + rt * (dcp / sc) * (dhp_big / sh)
    )


def delta_e_hex(hex1: str, hex2: str) -> float:
    return ciede2000(srgb_to_lab(hex_to_srgb(hex1)), srgb_to_lab(hex_to_srgb(hex2)))


__all__ = [
    "ciede2000",
    "delta_e_hex",
    "hex_to_linear",
    "hex_to_srgb",
    "linear_to_srgb",
    "srgb_to_hex",
    "srgb_to_lab",
    "srgb_to_linear",
]

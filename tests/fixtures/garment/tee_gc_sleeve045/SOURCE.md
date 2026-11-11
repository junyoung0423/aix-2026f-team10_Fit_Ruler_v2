# 출처: GarmentCode (견본 3 — 소매 길이만 바꾼 기본 티셔츠)

이 폴더의 파일은 GarmentCode 기본 디자인 파일에서 **한 줄만 바꾼 사본**과, 그 파일로 GarmentCode가 생성한 패턴이다.
패턴 파일은 손으로 고치지 않았다.

- 저장소: https://github.com/maria-korosteleva/GarmentCode
- 커밋: d449629 (Merge pull request #50 from llada60/fix_uv_connected)
- 라이선스: MIT (아래 전문)

| 파일 | 출처 | sha256 |
|---|---|---|
| `t-shirt_sleeve045.yaml` | GarmentCode `assets/design_params/t-shirt.yaml`(sha256 `54df2636…`)의 230번째 줄 `design.sleeve.length.v`만 `0.3 → 0.45`로 바꾼 사본. 줄바꿈(CRLF) 등 나머지 바이트는 원본과 같다 | `17e20062…` |
| `pattern_specification.json` | 위 yaml + 기준 몸 `assets/bodies/mean_all.yaml`(sha256 `fc78250c…`)로 `pattern_fit.py generate`가 만든 패턴(2026-11-10) | `afe76433…` |

**바꾼 이유**
- 0.45는 yaml range [0.1, 1.15] 안의 값이다.
- 반소매 티셔츠 범위를 유지한다(소매 22.05 → 28.89 cm). 그래서 `garment_family`는 그대로 basic_short_sleeve_crew다.
- 소매 천이 약 1.5배 늘어, 몸에서 떨어져 매달리는 소매와 진동 봉제선 닫힘을 drape_v1이 견디는지 본다.
- 바꾼 파라미터는 C3 맞춤에서 쓴 것 중 하나(sleeve.length)이고, 다른 측정값(가슴·어깨·기장)에는 걸리지 않는다(C3 민감도 표).

**치수**
- 이 패턴의 치수는 SKU 실측이 아니다. `samples/garment_v1.json`과 연결되지 않는다.
- 패턴 측정값(cm): 가슴 104.833, 어깨 34.457, 소매 28.888, 기장 49.963. 자기 교차는 없다.

**다시 만들기** (cwd·PYTHONPATH = `C:\dev\GarmentCode`):

```powershell
C:\dev\venvs\garmentcode\Scripts\python.exe C:\dev\FIT_RULER-c\pipelines\garment\pattern_fit.py generate C:\dev\FIT_RULER-c\pipelines\garment\fixtures\tee_gc_sleeve045\t-shirt_sleeve045.yaml C:\dev\GarmentCode\assets\bodies\mean_all.yaml <out_dir>
```

GarmentCode는 패널·봉제선 저장 순서가 실행마다 바뀔 수 있다. 같은 패턴인지는 파일 sha256 대신 정규형 sha256(`pattern_measure.canonical_sha256`)으로 확인한다.
이 패턴의 정규형 sha256: `941b5267d4886041c752bc138f9913bce3560d8240183c6af6e96e373c8fb581`.

## MIT License (C:\dev\GarmentCode\LICENSE 전문)

```
MIT License

Copyright (c) 2024 Maria Korosteleva

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

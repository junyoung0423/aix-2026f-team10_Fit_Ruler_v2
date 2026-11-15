# 출처: GarmentCode (견본집 tee_gc_v1)

이 폴더의 패턴은 GarmentCode로 생성한 것이다. 우리가 손으로 고치지 않았다.
`python -m tools.build_sample_catalog --config config/samples/tee_gc_v1.yaml`가 만들었다.

- 저장소: https://github.com/maria-korosteleva/GarmentCode
- 커밋: d449629 (d449629979028123a5c4dc9e732a2ec19b7fce31)
- 라이선스: MIT (아래 전문)

| 파일 | 출처 |
|---|---|
| `{sample_id}/design.yaml` | GarmentCode 기본 디자인 값 `assets/design_params/t-shirt.yaml`(sha256 `54df2636…`)에서 `shirt.width`, `shirt.length`, `sleeve.length`만 목표 치수에 맞게 바꾼 사본(`pattern_fit.fit_design`) |
| `{sample_id}/specification.json` | 위 yaml + 기준 몸 `assets/bodies/mean_all.yaml`(sha256 `fc78250c…`)로 GarmentCode가 생성한 패턴(`pattern_fit generate`, 2026-11-14). 파일 바이트를 그대로 둔다 |
| `{sample_id}/sample.json`, `catalog.json` | 위 패턴을 `pattern_measure`로 잰 값과 생성 기록. 우리가 만든 파일이다 |

이 패턴의 치수는 GarmentCode 기준 몸(`mean_all`, 실제 사용자가 아님)으로 만든 견본 치수이며 의류 실측이 아니다.
GarmentCode는 패널·봉제선 저장 순서가 실행마다 바뀔 수 있다. 같은 패턴인지는 파일 sha256 대신
정규형 sha256(`pattern_measure.canonical_sha256`, sample.json의 `pattern_sha256`)으로 확인한다.

## MIT License (GarmentCode LICENSE 전문)

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

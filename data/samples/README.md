# 패턴 견본집 (#4)

GarmentCode로 만든 반팔 티셔츠 견본 패턴이다. 의류를 등록할 때 견본 검색(#5)으로 가장 비슷한 견본을 고르고,
그 복사본을 맞춤(#6)의 시작 패턴으로 쓴다. 견본 값과 범위는 `docs/decisions.md`의 견본집 결정(2026-11-14)을 따른다.

| 폴더 | 설정 | 견본 |
|---|---|---|
| `tee_gc_v1/` | `config/samples/tee_gc_v1.yaml` | 12개: 가슴 100·104·108·112·118·126 cm × 기장 2가지 |

## 만들기·검사

```powershell
# 만들기: GarmentCode용 Python 3.9 (docs/spikes/garmentcode-blender.md 6절), 저장소 루트에서
<venv>\Scripts\python.exe -m tools.build_sample_catalog --config config\samples\tee_gc_v1.yaml [--jobs 4] [--force]
# 검사: 저장소 .venv, GarmentCode 없이 (CI 테스트도 같은 검사를 돌린다)
python -m tools.check_sample_catalog data\samples\tee_gc_v1
# 검색에 쓰기
python -m tools.search_sample --samples data\samples\tee_gc_v1\catalog.json --garment data\garments\tee_regular_m_navy_v1.json
```

- 출력 폴더가 있으면 덮어쓰지 않는다. `--force`일 때만 지우고 다시 만든다.
- 이 PC(i5-1135G7)에서 `--jobs 4`로 약 43분 걸렸다(견본 하나 맞춤 6~16분). GarmentCode 패턴 1회 생성이 약 12초다.
- 같은 설정으로 다시 만들면 패턴 해시(`pattern_sha256`)가 같다(C3 확인). 파일 바이트는 GarmentCode 저장 순서 때문에
  다를 수 있어 정규형 해시로 비교한다.

## 폴더 구조

```
tee_gc_v1/
  catalog.json            견본 검색 형식 + 생성 기록
  SOURCE.md               GarmentCode 출처·커밋·MIT 라이선스
  {sample_id}/
    design.yaml           맞춘 GarmentCode 디자인 파라미터 (생성 파라미터)
    specification.json    GarmentCode가 만든 패턴(도면). 바이트 그대로 둔다(.gitattributes -text)
    sample.json           견본 한 개의 치수·잔여 오차·생성 기록
```

## `catalog.json`

| 키 | 뜻 |
|---|---|
| `samples` | `[{sample_id, measurements}]`. 견본 검색(`fitting.catalog.search.SampleCandidate`, 다른 키 없음) 형식. `measurements`는 `GarmentMeasurements`(cm, 가슴은 둘레, 허리는 null) |
| `catalog`, `version` | 견본집 이름·버전 (설정의 `name`·`version`) |
| `note` | 설명 한 줄 |
| `sample_config`, `pattern_fit_config` | 설정 파일 `{path, name, version, sha256}` |
| `generation_body` | 생성용 몸 `{name, body_yaml, body_yaml_sha256, chest_cm}` (GarmentCode `mean_all`) |
| `generation_body_chest_cm` | style 구간의 기준 가슴둘레(cm, 99.8) |
| `style_bands` | style 구간 `[{style, lower, upper, include_lower, include_upper}]` (견본 가슴 − 기준 가슴, cm) |
| `garmentcode` | `{commit, base_design_yaml, base_design_sha256}` |
| `tolerance_cm` | 가슴·소매·기장 허용 잔여 오차(1.0 cm) |
| `created_at` | 만든 날짜(YYYY-MM-DD) |
| `excluded` | 목표에 닿지 못해 뺀 견본 `[{sample_id, reason}]` |

## `{sample_id}/sample.json` (A4 관리 화면이 읽는다)

| 키 | 뜻 |
|---|---|
| `catalog` | 견본집 이름 |
| `sample_id` | `tee_gc_{style}_{두 자리 번호}`. 번호는 가슴 오름차순, 그다음 기장 오름차순 |
| `style` | `slim`·`regular`·`relaxed`·`oversized` (화면 `web/src/labels.ts`와 같은 키). 패턴 구분이며 사용자별 핏 판정이 아니다 |
| `targets_cm` | 목표 `{chest_girth_cm, body_length_cm, sleeve_length_cm}` |
| `measurements_cm` | 잰 치수 `{chest_girth_cm, waist_girth_cm(null), shoulder_width_cm, sleeve_length_cm, body_length_cm}`. 소수 셋째 자리. `catalog.json`의 `measurements`와 같다 |
| `residuals_cm` | 잔여 오차 = 잰 치수 − 목표 (가슴·기장·소매) |
| `tolerance_cm` | 허용 잔여 오차(1.0 cm) |
| `shoulder` | `{judged: false, shoulder_width_cm}`. 어깨는 맞추지 않고 판정하지 않는다(GarmentCode가 어깨를 바꾸지 못한다). 등록 의류와 견본의 어깨 차이는 C4 의류별 기록에 둔다 |
| `pattern_sha256` | 패턴 정규형 sha256 (`pattern_measure.canonical_sha256`) |
| `files` | `{design, specification}` 파일 이름 |
| `design_params` | 맞춘 파라미터 `{shirt.width, shirt.length, sleeve.length}` (GarmentCode 비율 값) |
| `params_at_bound` | 파라미터가 yaml 범위 끝에 닿았는지 |
| `self_intersecting` | 패턴 자기 교차 여부 (true면 견본집에 들어오지 않는다) |
| `optimizer` | `{method, nfev, status, message}` (scipy `least_squares`) |
| `generation_body`, `garmentcode`, `pattern_fit_config`, `sample_config` | `catalog.json`과 같은 생성 기록 |
| `created_at` | 만든 날짜(YYYY-MM-DD) |

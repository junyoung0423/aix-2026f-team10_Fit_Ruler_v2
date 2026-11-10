# 스파이크 기록: GarmentCode 패턴 → Blender 천 착장 (C, T02·T10 일부)

- 작성: C (의류와 착장 렌더) · 2026-11-10 · 기록 근거: C 리뷰 기록 C1·C2·C3·Csync
- **이 문서의 숫자는 모두 명세서의 초기 검증값으로 판정한 시험 결과입니다. 검증된 성능이 아닙니다.**
- 몸은 GarmentCode 기준 몸 `mean_all` 하나만 썼습니다(사용자 몸이 아니고, D의 T07 몸도 아닙니다).

## 1. 흐름

```
SKU 목표 실측 (samples/garment_v1.json, 지금은 시연용 값)
   │  pattern_fit.py fit : 디자인 파라미터만 yaml 범위 안에서 조정 (몸은 mean_all 고정)
   ▼
GarmentCode 패턴 (패널 2D 외곽선 + 봉제선, cm)      ← pattern_measure.py 로 패턴 위에서 실측 재확인 (±1.0 cm)
   │  export_panels.py : 패널을 2 cm 삼각형 메시로, 봉제선을 정점 쌍으로
   │  좌표 변환 coords.py : GarmentCode(Y 위, 정면 +Z, 옷 cm·몸 m) → 명세서(Z 위, 정면 −Y, m)
   │                       회전 [[1,0,0],[0,0,-1],[0,1,0]], 옷 ×0.01  (변환 행렬은 drape_input.json에 기록)
   ▼
Blender 5.2 cloth (blender_drape.py, 설정 configs/drape_v1.json)
   │  1단계 봉제: 중력 0, 봉제 스프링으로 패널을 몸 둘레로 당김 (프레임 0–80)
   │  용접 weld.py: 봉제 쌍 정점을 하나로 합침 (용접 전 벌어짐 ≤ 10 mm 일 때만)
   │  2단계 착장: 중력 켜고 최대 300프레임 중 안정될 때까지 (지금 설정은 110프레임)
   ▼
검사 metrics.py (Blender 밖에서도 같은 메시 → 같은 결과)
   │  안정 / 침투 / 신장 / NaN / 봉제선 닫힘  → 하나라도 어기면 DRAPE_INVALID
   ▼
확인용 정면 PNG (runs 폴더에만 저장, git에 넣지 않음)
```

## 2. 견본 3개 결과 (drape_v1, 기준 몸 mean_all)

| | 견본 1: 기본 티셔츠 | 견본 2: SKU 맞춤 0.1.0-draft | 견본 3: 소매 길이만 변경 |
|---|---|---|---|
| 패턴 파일 | `fixtures/tee_gc_default/` | `garments/skus/tee_regular_m_navy_v1/` | `fixtures/tee_gc_sleeve045/` |
| 만든 방법 | GarmentCode 기본 디자인 그대로 | 가슴·소매·기장을 목표에 맞춤 (`pattern_fit.py fit`) | 기본 디자인에서 `sleeve.length` 0.3 → 0.45 (범위 0.1–1.15) |
| 패턴 실측 (가슴/어깨/소매/기장 cm) | 104.8 / 34.5 / 22.0 / 50.0 | 108.0 / **34.5 (목표 46.0)** / 22.0 / 70.0 | 104.8 / 34.5 / 28.9 / 50.0 |
| 패턴 판정 | — (목표 없음) | **FIT_FAILED** (어깨 −11.5 cm) | — (목표 없음) |
| 용접 전 최대 벌어짐 (≤ 10 mm, C 제안) | 6.81 mm ✅ | **12.59 mm ❌** → 용접 안 함 | 8.70 mm ✅ |
| 안정 (≤ 1 mm/프레임) | 0.009 ✅ | (중단) | 0.204 ✅ |
| 침투 (3 mm보다 깊은 면적 ≤ 1 %) | 0 % ✅ | 0 % ✅ | 0 % ✅ |
| 최대 신장 (≤ 1.5) | 1.454 ✅ (여유 작음) | **1.601 ❌** | **1.697 ❌** |
| NaN | 없음 ✅ | 없음 ✅ | 없음 ✅ |
| **착장 판정** | **통과** | **DRAPE_INVALID** | **DRAPE_INVALID** |
| 정면 PNG (git 밖) | `C:\dev\fitruler_c\runs\C2\v1_a\front.png` | `C:\dev\fitruler_c\runs\C3\c3_v1\front.png` | `C:\dev\fitruler_c\runs\C2PR\sleeve045_v1\front.png` |

견본 3에 대해:
- **만든 이유**: 반소매 범위 안(소매 22 → 29 cm)에서 소매 천만 1.5배로 늘렸습니다. 매달리는 소매와 진동 봉제선을 drape_v1이 견디는지 보려는 것입니다.
- **결과**: 신장 기준을 넘은 변이 4개 패널에 1개씩 있습니다(오른 뒤소매 1.697, 오른 앞판 1.682, 왼 앞판 1.641, 왼 앞소매 1.640).
  - 모두 진동·어깨 꼭대기 근처입니다.
  - 고치지 않고 그대로 기록했습니다. 견본 1도 여유가 작았던(1.454) 같은 부위입니다.
- 명령은 `<repo>`에서 실행합니다. 자리표시는 `pipelines/garment/README.md` "빠른 시작"과 같습니다.
- **생성 명령**:
  - `<venv>\Scripts\python.exe pipelines\garment\pattern_fit.py generate pipelines\garment\fixtures\tee_gc_sleeve045\t-shirt_sleeve045.yaml <GarmentCode>\assets\bodies\mean_all.yaml <runs>\gen_sleeve045 --gc-root <GarmentCode>`
- **착장 명령**:
  - `<venv>\Scripts\python.exe pipelines\garment\run_drape.py --spec pipelines\garment\fixtures\tee_gc_sleeve045\pattern_specification.json --body <GarmentCode>\assets\bodies\mean_all.obj --config pipelines\garment\configs\drape_v1.json --name sleeve045_v1 --out-root <runs>\C2PR --gc-root <GarmentCode> --blender "<blender.exe>"`

## 3. 되는 것 / 안 되는 것

되는 것
- GarmentCode 패턴을 Blender로 옮겨 기준 몸에 입히고, 명세서 6장 검사를 자동으로 판정합니다(견본 1 통과).
- 같은 입력이면 착장 좌표가 비트 단위로 같았습니다. 이 PC(Windows, i5-1135G7, Blender 5.2.3)에서 C2, C3, Csync에 걸쳐 3번 확인했습니다. 다른 PC에서도 같은지는 확인하지 않았습니다.
- 재질·마찰·질량·시뮬레이션 값은 버전 있는 설정 파일에 있습니다. 결과에 설정 이름·버전·sha256이 남습니다.
- 패턴 위에서 가슴·어깨·소매·기장을 재고, 목표에 맞게 디자인 파라미터를 조정합니다(가슴·소매·기장 오차 0.0 cm).

안 되는 것 / 아직 모르는 것
- **어깨를 맞출 수 없습니다(견본 2 FIT_FAILED).**
  - GarmentCode 티셔츠의 어깨점은 몸의 `shoulder_w`로만 정해집니다(`(shoulder_w − 2)/2`).
  - 이 위치를 바꾸는 디자인 파라미터가 없습니다. 목표 46 cm 같은 드롭 숄더를 표현하지 못합니다.
- **맞춘 패턴이 착장에 실패합니다(견본 2 DRAPE_INVALID).**
  - 봉제 단계에서 벌어짐이 6.5–13.2 mm 사이로 흔들리다가, 용접 시점(프레임 80)에 12.59 mm입니다.
- **신장 여유가 작습니다.** 견본 1은 1.454로 통과했지만, 소매만 길게 한 견본 3은 1.697로 실패했습니다.
  - 짧은 봉제선 주변의 짧은 변이 비율로 크게 늘어납니다.
- **중력 처짐은 검증 전입니다.** 용접 뒤 옷 전체가 약 0.10 kg로 천 강성에 비해 가볍습니다.
  - 중력을 켠 뒤 밑단이 0.5 mm만 내려갑니다. 실제 면 저지라면 더 처질 것으로 보지만 확인하지 않았습니다.
- **시간이 흔들립니다.** 같은 입력·같은 결과인데 실행마다 다릅니다(10분 제한 안). 원인은 확인하지 않았습니다.

  | 실행 | 시뮬레이션 s | Blender 착장 단계 s |
  |---|---|---|
  | 견본 1 (C2, C3, Csync) | 76.1 / 69.6 / 151.8 | 88.7 / 81.0 / 193.8 |
  | 견본 3 | 107.0 | 122.5 |

- 기준 몸은 3개 중 1개만 돌렸습니다. 명세서 6장 렌더(768×1024, 121프레임 MP4, 마스크, 깊이)는 아직 없습니다.

## 4. 다음 할 일

1. **C3b**: 어깨를 표현할 생성용 몸(아래 합의 1), 봉제 단계 수렴(용접 시점·봉제 구간·감쇠를 한 번에 하나씩), 허리 96 cm를 맞춤 대상에 추가.
2. **기준 몸 3개**: `mean_all`·`mean_female`·`mean_male`에 같은 패턴을 다시 배치해 착장합니다. 옷 치수는 바꾸지 않습니다(R06).
3. **렌더**: 명세서 6장 표대로 정면 고정 카메라, 무음 H264 MP4, 의류 마스크·깊이, 버전 고정 조명·재질.

## 5. 팀 합의가 필요한 것 (C 제안 전부, 담당 표시)

| # | 항목 | 지금 C가 쓰는 값 / 제안 | 담당 |
|---|---|---|---|
| 1 | 어깨를 맞추는 방법 | (a) SKU 맞춤용 생성 몸 `shoulder_w` 따로 두기, (b) GarmentCode를 감싸 드롭 숄더 몸판 추가, (c) 실물 실측 뒤 판단 | 전원 |
| 2 | 좌표·축·기준점·manifest 형식 | `drape_input.json`의 변환 기록(초안). 바닥 Z=0·몸 중심 X=0 정렬 이동은 지금 0 | D |
| 3 | 착장 시간 초과 오류 코드 | 지금은 `DRAPE_INVALID`(사유: 시간 초과). 제안: `DRAPE_TIMEOUT` | B |
| 4 | 결과 지표 JSON 필드 이름 | C 초안 (`drape_metrics.json`) | B |
| 5 | 초기 배치 침투 오류 기준 | 기록만 함. 제안: 3 mm 깊이, 면적 기준은 팀이 결정 | 전원 |
| 6 | 찢어짐 판정 기준 | 대리 지표는 봉제선 벌어짐뿐, 기준값 없음 | 전원 |
| 7 | 봉제선 닫힘 기준 | 용접 없이 ≤ 2.0 mm, 용접 방식이면 용접 전 ≤ 10 mm | 전원 |
| 8 | 용접 휴지 길이 편차 한도 | 판정에 안 넣음. 결과를 보고 정하지 않도록 팀이 먼저 정하길 제안 | 전원 |
| 9 | 2단계(봉제 → 용접 → 착장)를 표준 절차로 | 지금 drape_v1 방식 | 전원 (초기 배치는 D) |
| 10 | 면밀도 | 0.18 kg/m² 가정값. 제안: `fabric_weight_g_m2` 필드 추가 | B |
| 11 | 렌더 재현성 확인 | PNG 메타데이터 끄기 또는 픽셀 비교 | C (D 확인) |
| 12 | 패턴 측정 정의를 측정 프로토콜에 추가 | `garments/MEASURING.md` 2장 | B |
| 13 | 착장 후 기장 비교 | 높이차가 아니라 천을 따라 잰 길이 | B |
| 14 | FIT_FAILED 패턴을 착장 시험에 써도 되는지 | 지금은 초안 시험용으로만 씀 | 전원 |
| 15 | 의류 허리 위치 정의 | 없음. 샘플은 시연용인데 `waist_position_matched: true` | B·D |
| 16 | 필드별 원래 값(단면 너비 등) 기록 칸 | 계약에 없음. 제안: SKU 폴더 측정 기록 + `notes` | B |
| 17 | 의류 어깨너비: 직선인지 곡선인지 | 직선(신체 `shoulder_cm`과 같게) | B |
| 18 | 소매길이 시작점 | 프로토콜은 어깨 봉제선, C 패턴은 소매 윗선 시작(0.6 cm 차이) | B (C가 영향 수치 제공) |
| 19 | 샘플 `READY`와 C 상태 대응 | 패턴 실패 → `FAILED` 또는 `DRAFT`. READY는 사람 검수 뒤에만 | B |
| 20 | 실물 측정 세부 위치 | 가슴 = 겨드랑이 1 cm 아래 등 (`garments/MEASURING.md` 1장) | B |

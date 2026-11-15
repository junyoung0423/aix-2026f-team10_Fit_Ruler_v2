# 스파이크 기록: GarmentCode 패턴 → Blender 천 착장 (C, T02·T10 일부)

- 작성: C (의류와 착장 렌더) · 2026-11-10 · 기록 근거: C 리뷰 기록 C1·C2·C3·Csync
- **이 문서의 숫자는 모두 명세서의 초기 검증값으로 판정한 시험 결과입니다. 검증된 성능이 아닙니다.**
- 몸은 GarmentCode 기준 몸 `mean_all` 하나만 썼습니다(사용자 몸이 아니고, D의 T07 몸도 아닙니다).
- 2026-11-11 C2(#4 #8): 이전 저장소 `aix-2026f-team10_Fit_Ruler`의 `pipelines/garment/`(브랜치
  `feat/T02-garment-pattern-drape`)에서 이 저장소로 옮겼습니다. 옮긴 위치는 아래 표, 설치·실행은 6절에 있습니다.
  아래 2~5절의 결과는 이전 저장소에서 낸 것이고, 옮긴 뒤 견본 1을 다시 돌린 결과는 6절 끝에 있습니다.
  - 2026-11-14 C3(#4): `pattern_fit.py`의 generate·fit을 `fitting/adapters/garmentcode/pattern_fit.py`로,
    `configs/pattern_fit_v0.json`을 `config/pattern/pattern_fit_v0.json`(0.2.0: 이전 저장소 경로 `targets_json`을 뺌,
    맞춤 값은 같음)으로 옮겼다. 견본집 결과는 7절.
  - 옮기지 않은 것(이전 저장소에만 있음): `pattern_fit.py sensitivity`,
    `garments/skus/`·`garments/MEASURING.md`(의류 등록은 `data/garments/` 형식을 씀).
  - 실험 설정은 이전 저장소 `pipelines/garment/configs/experiments/`에 있다(C2 e01~e08, C3).

| 이전 (`pipelines/garment/`) | 새 위치 |
|---|---|
| `export_panels.py`, `pattern_measure.py`, `coords.py` | `fitting/adapters/garmentcode/` |
| `blender_drape.py`, `run_drape.py`, `drape_config.py`, `drape_measure.py`, `weld.py`, `metrics.py`, `analyze_drape.py` | `fitting/adapters/blender/` |
| `configs/drape_v0.json`, `configs/drape_v1.json` | `config/drape/` |
| `pattern_fit.py` (generate·fit, C3) | `fitting/adapters/garmentcode/pattern_fit.py` |
| `configs/pattern_fit_v0.json` (C3) | `config/pattern/pattern_fit_v0.json` |
| `fixtures/tee_gc_default/`, `fixtures/tee_gc_sleeve045/` | `tests/fixtures/garment/` |
| `tests/` | `tests/garment/` |
| `tools/make_config.py` | `tools/make_drape_config.py` |

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
Blender 5.2 cloth (blender_drape.py, 설정 config/drape/drape_v1.json)
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
| 패턴 파일 | `tests/fixtures/garment/tee_gc_default/` | `garments/skus/tee_regular_m_navy_v1/` (이전 저장소) | `tests/fixtures/garment/tee_gc_sleeve045/` |
| 만든 방법 | GarmentCode 기본 디자인 그대로 | 가슴·소매·기장을 목표에 맞춤 (`pattern_fit.py fit`, 이전 저장소) | 기본 디자인에서 `sleeve.length` 0.3 → 0.45 (범위 0.1–1.15) |
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
- 명령은 `<repo>`에서 실행합니다. 자리표시는 6절 "설치·실행"과 같습니다.
- **생성 명령** (C3에서 이 저장소로 옮겼다. 이전에는 이전 저장소의 `pipelines\garment\pattern_fit.py generate`):
  - `<venv>\Scripts\python.exe -m fitting.adapters.garmentcode.pattern_fit generate tests\fixtures\garment\tee_gc_sleeve045\t-shirt_sleeve045.yaml <GarmentCode>\assets\bodies\mean_all.yaml <runs>\gen_sleeve045 --gc-root <GarmentCode>`
- **착장 명령** (이 저장소):
  - `<venv>\Scripts\python.exe -m fitting.adapters.blender.run_drape --spec tests\fixtures\garment\tee_gc_sleeve045\pattern_specification.json --body <GarmentCode>\assets\bodies\mean_all.obj --config config\drape\drape_v1.json --name sleeve045_v1 --out-root <runs>\C2PR --gc-root <GarmentCode> --blender "<blender.exe>"`

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

## 6. 설치·실행

이전 `pipelines/garment/README.md`의 설치·실행 방법을 새 위치에 맞게 옮겼습니다.

경로는 자리표시입니다: `<repo>` 이 저장소 루트, `<venv>` GarmentCode용 Python 3.9 가상환경, `<GarmentCode>` GarmentCode 루트,
`<blender.exe>` Blender 5.2 실행 파일, `<runs>` 산출물 폴더(git 밖).
(C의 개발 PC 예: `<repo>`=`C:\dev\fitruler-c2`, `<venv>`=`C:\dev\venvs\garmentcode`, `<GarmentCode>`=`C:\dev\GarmentCode`,
`<blender.exe>`=`C:\Program Files\Blender Foundation\Blender 5.2\blender.exe`, `<runs>`=`C:\dev\fitruler_c\runs`)

### 두 가지 Python 환경

| 환경 | 용도 | 설치 |
|---|---|---|
| 저장소 `.venv` (Python 3.10 이상) | 단위 테스트, ruff | `pip install -e ".[dev,garment]"` (garment 없이 `.[dev]`만 깔면 garment 테스트는 건너뜀) |
| `<venv>` (Python 3.9) | GarmentCode export, `run_drape`, `analyze_drape` | 아래 "새 PC에서 설치" |
| Blender 5.2 내장 Python | `blender_drape.py` | Blender 설치만. 스크립트가 저장소 루트를 직접 import 경로에 넣는다 |

`<venv>`가 Python 3.9인 것은 이전 스파이크를 그대로 따른 것입니다. 그래서 `fitting/adapters/garmentcode/`와
`run_drape`·`analyze_drape`·`drape_config`·`metrics`·`drape_measure`·`weld`는 Python 3.9 문법을 지킵니다.

### 새 PC에서 설치

Windows PowerShell 기준입니다. 경로에 한글·공백이 없는 곳(예: `C:\dev\...`)을 권합니다. C 확장 라이브러리와 Blender가 한글 경로에서 문제를 일으킨 적이 있습니다.

```powershell
# 1) Python 3.9 가상환경 (python.org 3.9.x 설치 뒤)
<python39>\python.exe -m venv <venv>

# 2) GarmentCode: clone, 커밋 고정, 설정 템플릿 복사 (원본 코드는 고치지 않는다)
git clone https://github.com/maria-korosteleva/GarmentCode.git <GarmentCode>
git -C <GarmentCode> checkout d449629979028123a5c4dc9e732a2ec19b7fce31
Copy-Item <GarmentCode>\system.template.json <GarmentCode>\system.json

# 3) Python 패키지 (이전 requirements.txt의 고정 버전) + pygarment(GarmentCode 편집 설치)
<venv>\Scripts\python.exe -m pip install numpy==1.26.4 scipy==1.13.1 trimesh==4.12.2 libigl==2.6.3 `
    cgal==6.0.1.post202410241521 svgpathtools==1.8.0 PyYAML==6.0.3 pytest==8.4.2
<venv>\Scripts\python.exe -m pip install -e <GarmentCode>

# 4) Blender 5.2 LTS를 blender.org에서 설치 (C 개발 PC: 5.2.3). 별도 설정은 없다.

# 5) 저장소 .venv (Python 3.10 이상)
cd <repo>
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev,garment]"
```

- GarmentCode 경로: `--gc-root` > 환경변수 `FIT_RULER_GC_ROOT` > `C:\dev\GarmentCode`.
- Blender 경로: `--blender` > 환경변수 `BLENDER_EXE` > `C:\Program Files\Blender Foundation\Blender 5.2\blender.exe`.
- 출력 루트: `--out-root` > 환경변수 `FIT_RULER_RUNS_DIR`. 둘 다 없으면 실행하지 않고 멈춥니다(기본 경로 없음).
- `run_drape`는 export를 GarmentCode 루트에서 실행하고(`system.json`이 상대경로라서) `PYTHONPATH`에 GarmentCode 루트와
  저장소 루트를 넣습니다. 그래서 `PYTHONPATH`는 따로 맞추지 않아도 됩니다. 편집 설치(.pth)만으로는 `import pygarment`가 되지 않습니다.
- GPU는 필요 없습니다. GarmentCode 자체 시뮬레이터(NVIDIA warp)는 쓰지 않습니다.
- libigl·cgal은 `<venv>`에만 설치합니다(이전 requirements.txt). 이 저장소의 `garment` 선택 의존성에는 넣지 않습니다.

### 테스트

| 필요한 것 | 테스트 (`tests/garment/`) | 실행 |
|---|---|---|
| `.[dev,garment]` | `test_coords`, `test_metrics`, `test_weld`, `test_drape_measure`, `test_pattern_measure` (42개) | `pytest` (CI와 같음). garment가 없으면 건너뜀 |
| + GarmentCode와 `<venv>` | `test_export` 3개 (마커 `garmentcode`) | `pytest -m garmentcode`. 하위 프로세스 python은 환경변수 `FIT_RULER_GC_PYTHON`(기본 `C:\dev\venvs\garmentcode\Scripts\python.exe`) |
| + Blender | `test_blender_import` 1개 (마커 `blender`): Blender 안에서 `blender_drape.py`의 패키지 import 확인 | `pytest -m blender` |

### 착장 1회 (약 2~6분)

`<repo>`에서 `<venv>`의 python으로 모듈로 실행합니다.

```powershell
<venv>\Scripts\python.exe -m fitting.adapters.blender.run_drape `
  --spec tests\fixtures\garment\tee_gc_default\t-shirt__261103-16-24-09_specification.json `
  --body <GarmentCode>\assets\bodies\mean_all.obj `
  --config config\drape\drape_v1.json --name try1 --out-root <runs>\quick `
  --gc-root <GarmentCode> --blender "<blender.exe>"
```

- 결과: `<runs>\quick\try1\drape_metrics.json`(`drape_ok`, `failed_checks`)과 `front.png`.
- 옵션: `--out-root`(없으면 환경변수 `FIT_RULER_RUNS_DIR`, 둘 다 없으면 오류), `--max-frames N`(짧은 시험용, 설정의 300 이하만), `--gc-root`, `--blender`.
  출력 폴더가 이미 있으면 덮어쓰지 않고 멈춥니다.

단계별로 따로 돌릴 때:

```powershell
# export (cwd = GarmentCode, PYTHONPATH = GarmentCode;저장소 루트)
cd <GarmentCode>; $env:PYTHONPATH = '<GarmentCode>;<repo>'
<venv>\Scripts\python.exe -m fitting.adapters.garmentcode.export_panels <spec.json> <body.obj> <out_dir> --config <drape_vN.json>
# 착장
& "<blender.exe>" --background --python <repo>\fitting\adapters\blender\blender_drape.py -- <out_dir>\drape_input.json <out_dir> <drape_vN.json> [max_frames]
# Blender 없이 지표 재계산 (cwd = 저장소 루트)
cd <repo>; <venv>\Scripts\python.exe -m fitting.adapters.blender.analyze_drape <out_dir>
# 설정에서 값 하나만 바꾼 실험 설정 만들기
python -m tools.make_drape_config config\drape\drape_v1.json <out.json> --name ... --version ... --description ... --set cloth.mass=0.0001
```

설정 값을 바꿀 때는 파일을 고치지 말고 새 버전 파일을 만듭니다. 한 번에 한 값만 바꿉니다.
모든 지표 JSON에 설정 파일 이름·버전·sha256이 들어갑니다. `config/drape/*.json`은 `.gitattributes`로 LF가 고정되어
체크아웃마다 같은 해시가 나옵니다. `tests/fixtures/garment/`는 GarmentCode가 만든 바이트(CRLF 포함)를 그대로 둡니다(`-text`).

### 산출물 (`<runs>\<단계>\<name>\`, git 제외)

| 파일 | 내용 |
|---|---|
| `drape_input.json` | 패널 메시, 봉제 쌍, 몸 메시, 변환 기록, 입력 sha256, 설정 기록 |
| `drape.blend` | 착장 장면 |
| `garment_draped.obj` | 착장된 옷 (Z 위, 6자리 반올림) |
| `garment_draped_co.npy` | 착장된 옷 정점 좌표 (float64 원값, 재계산·결정성 비교용) |
| `garment_ref_co.npy` | 중력 반응 기준 프레임(용접 직후) 좌표 |
| `drape_trace.json` | 프레임별 이동량 중앙값, 최종 면 중심 부호 거리 |
| `drape_metrics.json` | 착장 검사 결과 + `error_code`, 설정 기록, 프레임·시간 |
| `analysis.json` | Blender 없이 다시 계산한 지표, 일치 여부, 패널·봉제선별 요약 |
| `front.png` | 착장 확인용 정면 이미지 (Workbench). 명세서 6장 렌더 클립이 아니다 |
| `run_summary.json`, `*_log.txt` | 단계별 시간·종료 코드, 상태, 오류 코드 |

오류 코드(명세서 12장): export 실패 `GARMENT_NOT_READY`, 검사 실패·착장 10분 초과 `DRAPE_INVALID`.

### 옮긴 뒤 재실행 (2026-11-11, 견본 1, drape_v1, mean_all)

새 위치의 `run_drape`로 한 번 돌려 이전 저장소 결과와 비교했습니다. 출력은 `C:\dev\fitruler_c\runs\C2_migrated\v1_a\`(git 밖)입니다.

| 지표 | 이전 C2 `v1_a` | 이전 Csync `sync_fx_v1` | 옮긴 뒤 `C2_migrated/v1_a` |
|---|---|---|---|
| frames | 110 | 110 | 110 |
| penetration_area_ratio | 0.0 | 0.0 | 0.0 |
| stretch_max | 1.454 | 1.454 | 1.454 |
| seam_gap_max_mm (용접 뒤) | 0.0 | 0.0 | 0.0 |
| seam_gap_preweld_max_mm | 6.81 | 6.81 | 6.81 |
| drape_ok | true | true | true |
| sim_seconds | 76.1 | 151.8 | 77.0 |

- 착장 좌표(`garment_draped_co.npy`)가 세 실행 모두 비트 단위로 같습니다. `garment_ref_co.npy`와 `analysis.json`(오프라인 재계산 일치, 착장 후 측정)도 Csync와 같습니다.
- `drape_metrics.json`은 Csync와 키가 모두 같고, 다른 값은 시간(`sim_seconds`, `weld.relax_seconds`)뿐입니다.
  C2 `v1_a`는 진단 키(`stretch_min`, `stretch_p01`, `gravity_response`)가 생기기 전 실행이라 그 키가 없습니다.

## 7. C3 견본집 착장 시험 (2026-11-14~15, drape_v1, 기준 몸 mean_all)

견본집 `tee_gc_v1`(`data/samples/tee_gc_v1/`) 12개를 drape_v1 그대로 한 번씩 착장했습니다. 실패가 나와도 착장 설정·코드는 고치지 않았습니다.
출력은 `C:\dev\fitruler_c\runs\C3\<견본 ID>\`(git 밖)입니다. 명령은 6절 "착장 1회"와 같고 `--spec`만 `data\samples\tee_gc_v1\<견본 ID>\specification.json`입니다.

| 견본 | 가슴/기장/소매 cm | 착장 판정 | 실패 항목 | 최대 신장 (≤ 1.5) | 용접 전 최대 벌어짐 mm (≤ 10) | 침투 | 시뮬레이션 s | 전체 s | analyze |
|---|---|---|---|---|---|---|---|---|---|
| `tee_gc_slim_01` | 100 / 66 / 22 | **DRAPE_INVALID** | 신장, 봉제 닫힘 | **2.181** | **10.08** | 0 % | 79.6 | 246.6 | 정상, Blender와 일치 |
| `tee_gc_slim_02` | 100 / 72 / 22 | **DRAPE_INVALID** | 신장 | **1.703** | 7.64 | 0 % | 212.2 | 630.6 | 정상, 일치 |
| `tee_gc_slim_03` | 104 / 66 / 22 | **DRAPE_INVALID** | 신장 | **1.587** | 5.68 | 0 % | 173.1 | 371.6 | 정상, 일치 |
| `tee_gc_slim_04` | 104 / 72 / 22 | 통과 | — | 1.474 | 6.50 | 0 % | 129.6 | 359.3 | 정상, 일치 |
| `tee_gc_regular_05` | 108 / 66 / 22 | **DRAPE_INVALID** | 신장 | **1.804** | 7.90 | 0 % | 230.9 | 726.6 | 정상, 일치 |
| `tee_gc_regular_06` | 108 / 72 / 22 | **DRAPE_INVALID** | 신장 | **1.554** | 8.90 | 0 % | 222.4 | 579.2 | 정상, 일치 |
| `tee_gc_regular_07` | 112 / 66 / 22 | **DRAPE_INVALID** | 신장 | **1.630** | 8.24 | 0 % | 135.3 | 418.0 | 정상, 일치 |
| `tee_gc_regular_08` | 112 / 72 / 22 | **DRAPE_INVALID** | 신장 | **1.560** | 8.04 | 0 % | 84.4 | 303.3 | 정상, 일치 |
| `tee_gc_relaxed_09` | 118 / 66 / 22 | 통과 | — | 1.316 | 8.15 | 0 % | 66.6 | 120.6 | 예외 (아래) |
| `tee_gc_relaxed_10` | 118 / 72 / 22 | **DRAPE_INVALID** | 봉제 닫힘 | 1.289 | **10.56** | 0 % | 39.9 | 96.7 | 예외 (아래) |
| `tee_gc_oversized_11` | 126 / 72 / 24 | 통과 | — | 1.441 | 6.15 | 0 % | 200.8 | 378.4 | 예외 (아래) |
| `tee_gc_oversized_12` | 126 / 78 / 24 | 통과 | — | 1.414 | 8.53 | 0 % | 188.4 | 341.9 | 예외 (아래) |

- 치수는 `sample.json`의 패턴 실측(목표와 0.01 cm 안)입니다. 전체 s는 export·drape·analyze 세 단계의 합입니다.
- 통과 4개, DRAPE_INVALID 8개(신장 7개, 봉제 닫힘 2개, slim_01은 둘 다)입니다. 모든 견본이 안정·침투·NaN 검사는 통과했습니다.
- 봉제 닫힘 실패 2개는 용접 시점(프레임 80)의 벌어짐이 10 mm를 넘어 용접하지 않은 경우입니다(2절 견본 2와 같은 모양).
- 기준 티셔츠의 검색 결과 견본(`docs/decisions.md` B3 기대 결과)은 둘 다 실패입니다. 티셔츠 1 → `tee_gc_regular_06`(신장 1.554), 티셔츠 2 → `tee_gc_relaxed_10`(봉제 닫힘 10.56 mm).

관찰
- **몸에 가까울수록 신장이 큰 경향은 있지만 단조롭지 않습니다.**
  - 가슴별 최대 신장(기장 짧은 것/긴 것): 100 cm 2.181/1.703, 104 cm 1.587/1.474, 108 cm 1.804/1.554, 112 cm 1.630/1.560, 118 cm 1.316/1.289, 126 cm 1.441/1.414.
  - 118 cm 이상은 모두 1.5 아래이고, 112 cm 이하는 104 cm 긴 기장(1.474) 하나만 1.5 아래입니다. 108 cm가 104 cm보다 큽니다.
- **같은 가슴에서는 6쌍 모두 기장이 긴 쪽의 신장이 작습니다.** 원인은 확인하지 않았습니다.
- **위치**: 1.5를 넘은 견본 7개 모두 가장 늘어난 변이 진동(소매와 앞판이 만나는 겨드랑이~어깨 꼭대기) 근처입니다.
  - 가장 늘어난 변의 좌표가 |x| 0.175~0.202 m, z 1.297~1.429 m(Blender 좌표, Z 위)입니다.
  - 1.5를 넘은 변이 있는 패널은 앞판(`*_ftorso`)과 소매(`*_sleeve_f`, `*_sleeve_b`)가 대부분이고, 뒤판은 slim_01에서만 넘었습니다.
  - 2절 견본 1·3에서 본 부위와 같습니다.
- 용접 전 벌어짐은 가슴과 뚜렷한 관계가 없습니다(5.68~10.56 mm).
- 시뮬레이션 시간이 39.9~230.9 s로 크게 다릅니다. 착장은 한 번에 하나씩 돌렸고, 3절처럼 원인은 확인하지 않았습니다.

analyze 예외 (고치지 않고 기록만 함)
- 가슴 118·126 cm 견본 4개에서 Blender 없이 다시 계산하는 analyze 단계가 기존 착장 후 측정 코드(`drape_measure.measure_draped`)의 예외로 끝났습니다. `analysis.json`이 없습니다.
  - 기장선의 점(패턴 좌표 x = 10.82 cm)이 앞판 메시 밖으로 판정됩니다: `ValueError: point [10.8188, y] is 1.002~1.060 cm outside panel left_ftorso`.
  - relaxed_09 1.031, relaxed_10 1.025, oversized_11 1.002, oversized_12 1.060 cm. 112 cm 이하 8개는 예외가 없습니다.
- 착장 판정은 Blender 단계의 `drape_metrics.json`으로 하므로 영향이 없습니다. 다만 이 4개는 오프라인 재계산이 Blender 지표와 같은지(`offline_matches_blender`) 확인하지 못했고, 착장 후 치수도 없습니다.

재현 확인 (2026-11-15)
- `tee_gc_slim_01`·`tee_gc_oversized_12`를 같은 설정(`config/samples/tee_gc_v1.yaml`, `pattern_fit_v0` 0.2.0, GarmentCode d449629)으로 임시 폴더에 다시 만들었습니다.
  - `build_sample_catalog`가 견본 하나에 하는 단계(`fit_all` → `design.yaml` → 새 프로세스 `generate` → 측정)를 두 견본에만 돌렸습니다. 생성 명령은 ID 번호 검사 때문에 일부 견본만 고른 설정을 받지 않습니다.
- 두 견본 모두 정규형 패턴 해시(`pattern_sha256`), 치수, 디자인 파라미터, `design.yaml` 바이트가 커밋된 것과 같았습니다. 임시 폴더는 지웠습니다.

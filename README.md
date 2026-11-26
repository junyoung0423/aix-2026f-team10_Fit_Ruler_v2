# AIMAX 가상 피팅 (Fit Ruler)

신체 치수와 등록 티셔츠의 실측을 비교해 핏 리포트를 주고, 3D 착장과 실사화 영상으로 확인하는 반팔 티셔츠 가상 피팅 프로젝트입니다.

## 문서

| 문서 | 역할 |
| --- | --- |
| [활동지 (week-07 수정안)](docs/plan/week-07-activity.md) | 무엇을 만드나: 범위, 이슈 #1~#13과 완료 조건, 마일스톤 |
| [3주 개발 계획](docs/plan/aimax-3week-plan.md) | 언제·어떻게: 역할, PR 목록, 리뷰 규칙, 주고받을 것, 위험과 대체안 |
| [개발 명세서 초안 v0.1](docs/AIMAX-specification-draft-v0.1.docx) | 세부 기준: 판정 규칙, 착장 점검 수치, 포트·API·오류 처리. 위 두 문서와 충돌하지 않는 부분은 그대로 따른다 |
| [결정 기록](docs/decisions.md) | 정한 기준과, 위 문서끼리 다른 부분을 어떻게 정했는지 |

## 목표

- **치수로 설명할 수 있는 핏 리포트:** 신체와 의류 실측의 차이, 부위별 판정과 근거를 제공한다.
- **선택한 옷의 치수를 유지하는 착장:** 사용자 치수로 몸을 만들고, 등록 의류에 맞춘 3D 옷을 입힌다.
- **실사화 결과의 품질 확인:** 렌더와 실사 영상의 색·실루엣을 비교하고, 기준을 넘으면 검증 실패로 표시한다.
- **독립적인 결과 제공:** 3D·실사화가 늦거나 실패해도 핏 리포트는 확인할 수 있다.

## 범위

| 항목 | 범위 |
| --- | --- |
| 의류 | 성인용 반팔 라운드넥 티셔츠 |
| 사용자 입력 | 키·가슴둘레·허리둘레·어깨너비·팔길이 (cm) |
| 의류 데이터 | AI Hub 의류 통합 데이터의 티셔츠 10~20벌 (치수·색·원단) |
| 패턴 견본집 | GarmentCode로 만든 반팔 라운드넥 패턴 10개 이상 |
| 수치 결과 | 부위별 신체 치수·옷 치수·여유량·판정·설명 |
| 시각 결과 | 3D 착장 미리보기, 정면 렌더 클립, 실사 영상과 대표 프레임, 색·실루엣 검증 결과 |

## 흐름

1. **의류 사전 준비**: AI Hub 티셔츠 데이터 등록(#3) → GarmentCode 패턴 견본집 생성(#4) → 치수가 비슷한 견본 검색(#5) → 실제 의류에 맞게 최적화(#6)
2. **피팅 요청**: 치수 입력 → 핏 리포트(#2) → SMPL 몸 생성(#7) → Blender 착장(#8) → 정면 렌더 클립·실사화(#10) → 대표 프레임 검증(#11)
3. 3D·실사화가 실패해도 핏 리포트는 제공합니다(#12).

## 저장소 구조

```text
web/        A  입력·결과·관리 화면
server/     B  FastAPI 핏 API, 피팅 작업 API
fitting/       공통 스키마·포트(domain, ports), 핏 계산(fit), 서비스, 견본(catalog),
               몸(body), 검증(verify), 어댑터(adapters/fake·aihub·garmentcode·blender·smpl·fal_ltx·…)
tools/         명령어: 견본집 생성·의류 등록, 피팅 1회 실행
config/        핏 기준표·허용 오차·검증 기준
data/          작은 메타데이터만 커밋
docs/          plan/(활동지·3주 계획), 명세서, decisions.md, spikes/, ux/
```

폴더별 담당과 협업 규칙은 [CONTRIBUTING.md](CONTRIBUTING.md)에 있습니다.

## 설치

처음 받는 사람이 위에서부터 따라 하면 됩니다. 핏 리포트·가짜 피팅·테스트는 1~3단계만으로 돌고, 실제 착장·렌더·몸·실사화는 쓰는 단계에 필요한 것만 더 받습니다. 무엇이 빠졌는지는 4단계 `check_env`가 알려 줍니다.

**Windows**: 저장소는 `C:\dev\...`처럼 영어·공백 없는 경로에 둡니다. Blender·OpenCV·C 확장이 한글 경로에서 실패한 적이 있습니다.

### 1. 필요한 프로그램

| 쓰는 곳 | 필요한 것 | 받는 법 |
|---|---|---|
| 핏 리포트, 가짜 피팅, 테스트 | Python 3.10 이상(3.13에서 확인), git | python.org |
| 웹 화면 | Node 20 이상 | [web/README.md](web/README.md) |
| 착장·렌더 (C4·C5) | Blender 5.2, GarmentCode와 그 Python 3.9 가상환경 | [docs/spikes/garmentcode-blender.md](docs/spikes/garmentcode-blender.md) 6절 |
| 몸 (D3) | SMPL 중성 모델. **각자** 가입·라이선스 동의 후 받고, 공유·커밋하지 않습니다 | [docs/spikes/smpl-body-measure.md](docs/spikes/smpl-body-measure.md) 4절 |
| 실사화 (D4) | fal API 키 `FAL_KEY`. 호출마다 비용이 듭니다 | 팀 담당자 |

### 2. Python 패키지

```powershell
python -m venv .venv
.venv\Scripts\activate                          # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev,garment,realize,body]"    # CI와 같은 묶음
```

선택 의존성 묶음은 `garment`(착장 계산: numpy·scipy·trimesh), `realize`(실사화·검증: fal-client·OpenCV), `body`(SMPL 몸: numpy·scipy·OpenCV)입니다. 전체 테스트는 CI처럼 네 묶음을 모두 깔고 돌립니다.

### 3. 환경변수 (`.env`)

```powershell
copy .env.example .env      # 쓸 값만 채운다. .env는 커밋하지 않는다
```

- `.env`는 **서버(`uvicorn server.app:app`)와 `tools.run_fitting_once`·`tools.check_env`·`tools.prepare_garments`** 가 시작할 때 읽습니다. 비어 있는 환경변수만 채우고, 이미 있는 환경변수가 우선합니다(명령 인자 > 환경변수 > `.env` > 기본값). 빈 값(`FAL_KEY=`)은 넣지 않습니다.
- 그 밖의 명령(`build_sample_catalog`, `build_body`, `render_clip` 등)은 `.env`를 읽지 않으니 환경변수나 인자로 줍니다. `realize_clip`·`spike_fal_ltx`는 원래대로 `FAL_KEY`를 `.env`에서 읽습니다.
- Blender 경로는 **`BLENDER_PATH`** 하나만 씁니다(예전 이름 `BLENDER_EXE`도 받지만 `check_env`가 주의를 띄웁니다).
- 다른 파일을 쓰려면 환경변수 `FIT_ENV_FILE`에 경로를, 끄려면 `off`를 줍니다(테스트는 `off`로 돕니다).
- 웹 화면 설정은 따로 `web/.env`입니다([web/README.md](web/README.md)).

### 4. 점검

```powershell
python -m tools.check_env                                   # 기본 설정(지금은 전부 가짜)
python -m tools.check_env --adapters drape_render_blender   # 착장·렌더까지 실제
python -m tools.check_env --adapters visual_real            # 영상 경로 5단계 모두 실제
python -m tools.check_env --adapters visual_fitroom         # 위와 같고 실사화만 피팅룸 영상
```

고른 어댑터 설정에 필요한 것만(Python·선택 의존성, Blender, GarmentCode 루트·Python 3.9, 준비된 의류, 개발용 몸, SMPL 모델 파일, `FAL_KEY`, Node) 표로 보여 주고, 빠진 것마다 고치는 방법을 한 줄씩 적습니다. 모두 있으면 서버와 같은 팩토리로 어댑터를 실제로 만들어 봅니다(fal은 부르지 않음). `FAL_KEY`는 **있음/없음만** 출력합니다. 빠진 것이 있으면 종료 코드 1입니다. 표는 마크다운이라 PR에 그대로 붙일 수 있습니다.

### 5. 린트·테스트

```powershell
$env:PYTHONUTF8 = "1"                  # Windows 권장 (아래)
ruff check .
pytest --basetemp=C:/dev/_pt/pt        # 사용자 폴더 이름에 한글이 있으면 영어 경로를 준다
```

- **Windows 출력 인코딩**: 한국어 Windows는 출력을 파일·파이프로 돌릴 때 cp949를 씁니다. `tools/` 명령은 시작할 때 출력을 UTF-8로 바꾸므로 `PYTHONUTF8` 없이도 "—" 같은 문자에서 멈추지 않습니다. 그래도 다른 스크립트의 cp949 문제를 피하려고 `PYTHONUTF8=1`을 켜 두기를 권합니다.
- **PowerShell 5.1에서 출력을 파일로 저장할 때**(`python -m tools.check_env > out.txt`): PowerShell이 명령 출력을 cp949로 다시 읽어 한국어가 깨질 수 있습니다. 먼저 `[Console]::OutputEncoding = [Text.Encoding]::UTF8`을 실행하거나 PowerShell 7을 씁니다.
- **한글 경로**: pytest 임시 폴더가 `C:\Users\<한글 이름>\...`이면 OpenCV가 그 경로를 읽지 못해 테스트 1개(`test_media_frames`)가 실패합니다. `--basetemp`에 영어 경로를 줍니다(`check_env`가 "임시 폴더" 주의로 알려 줌).
- Blender·GarmentCode·SMPL·fal이 필요한 테스트는 마커로 빠집니다. 준비된 PC에서는 `pytest -m blender`(또는 `garmentcode`, `smpl`)로 돌립니다. **`-m fal`은 실제 호출이라 비용이 듭니다.**

## 의류 사전 준비 (견본집 → 등록 → 검색 → 최적화)

3D 착장 전에 한 번 해 두는 일입니다. 견본집과 시연 의류 2벌의 준비 결과는 저장소에 이미 있어서, **기준 사례만 돌릴 때는 건너뛰어도 됩니다.** AI Hub 의류를 새로 넣거나 설정을 바꿀 때 순서대로 다시 합니다. 단계마다 쓰는 Python이 다르고(1단계만 GarmentCode용 3.9) 입력이 저장소 밖에 있어서, 묶는 명령은 따로 두지 않았습니다.

| 단계 | 명령 (저장소 맨 위에서) | 입력 | 출력 | 걸리는 시간 | 필요한 것 |
|---|---|---|---|---|---|
| 1. 견본집 (#4) | `<GarmentCode venv>\Scripts\python.exe -m tools.build_sample_catalog --config config\samples\tee_gc_v1.yaml --jobs 4`<br>확인: `python -m tools.check_sample_catalog data\samples\tee_gc_v1` | `config/samples/tee_gc_v1.yaml`, GarmentCode | `data/samples/tee_gc_v1/` (견본 12개, 커밋함) | 약 43분(`--jobs 4`, i5-1135G7). 확인은 수 초 | GarmentCode, Python 3.9 |
| 2. 의류 등록 (#3) | `python -m tools.register_aihub_garment <라벨.json> --out C:\dev\aihub\garments`<br>확인: `python -m tools.check_garments C:\dev\aihub\garments --min-ready 10` | AI Hub 라벨 JSON(저장소 밖) | `{out}/{id}.json` 1벌 | 1벌 수 초 | 없음 |
| 3. 견본 검색 (#5) | `python -m tools.search_sample --samples data\samples\tee_gc_v1\catalog.json --garment data\garments\tee_regular_m_navy_v1.json` | 의류 JSON, `catalog.json` | 표준 출력에 고른 견본·순위 JSON | 수 초 | 없음 (4단계가 같은 검색을 다시 하므로 확인용) |
| 4. 최적화 (착장 준비, #6) | `python -m tools.prepare_garments [--garments C:\dev\aihub\garments --out C:\dev\aihub\prepared] [--jobs 4] [--only ID ...]` | 의류 폴더, 견본집 | `{준비 폴더}/{id}/prep.json`·`design.yaml`·`specification.json` | 1벌 수 분~십수 분 (입력이 같으면 건너뜀) | GarmentCode, Python 3.9 (`FIT_GC_PYTHON`, `FIT_RULER_GC_ROOT`) |

- 시연 의류 2벌은 `data/garments/`·`data/prepared/`에 있습니다. AI Hub 의류와 그 준비 결과는 저장소 밖에 두고 `FIT_GARMENTS_DIR`·`FIT_PREPARED_DIR`로 알려 줍니다(이용 조건 확인 전까지 커밋하지 않음, `docs/decisions.md`).
- 준비 실패(`prep.json`의 `status: FAILED`)는 그 의류의 3D 착장만 못 한다는 뜻입니다. 핏 리포트는 됩니다. 기록 키는 [data/prepared/README.md](data/prepared/README.md), 견본집은 [data/samples/README.md](data/samples/README.md)에 있습니다.

## 피팅 1회 실행 (기준 사례)

서버 없이 한 프로세스 안에서 기준 사례(신체 1: 175·100·80·40·56 cm × `tee_regular_m_navy_v1`) 피팅을 끝까지 돌립니다. 어댑터 설정을 아래 표 순서로 하나씩 늘려 가며 확인하고, 돌리기 전에 같은 `--adapters`로 `check_env`를 먼저 돌립니다.

```powershell
python -m tools.check_env --adapters drape_render_blender
python -m tools.run_fitting_once --adapters drape_render_blender
```

| `--adapters` | 실제로 도는 단계 | 더 필요한 것 (`check_env`가 보는 것) | 더 생기는 결과 | 걸리는 시간 | 비용 |
|---|---|---|---|---|---|
| `fake` (지금 `default`와 같음) | 없음 (핏 리포트만 실제) | Python 패키지만 | 핏 리포트, 단계별 상태 | 수 초 | 없음 |
| `drape_blender` | 착장 | Blender(`BLENDER_PATH`), GarmentCode 루트·Python 3.9, 준비된 의류, 개발용 몸 `FIT_DRAPE_BODY_OBJ`(몸이 가짜라 몸 메시가 없음) | `*_drape_result.blend`, `*_drape_preview.png` | 보통 4~8분 | 없음 |
| `drape_render_blender` | 착장 · 렌더 | 위와 같음 | + `*_render_clip.mp4`, `*_render_mask.png` | + 약 1분 | 없음 |
| `smpl_body` | 몸 | SMPL 모델(`SMPL_MODEL_DIR`), `.[body]` | `*_body_mesh.obj` | 약 20초 | 없음 |
| `visual_real` | 몸 · 착장 · 렌더 · 실사화 · 검증 | 위 전부(개발용 몸은 비움) + `FAL_KEY`, `.[realize]` | + `*_video.mp4`, `*_frame.png`, `*_render_frame.png`, 검증 PASS·FAIL | 약 10분 [추정: 끝까지 돌린 기록 없음] | 실사화 1회 약 $0.2. `--consent --yes`가 있어야 돈다 |
| `visual_fitroom` | `visual_real`과 같고 실사화만 피팅룸 영상(사진 → 팔 내린 자세 사진 → Veo 3.1 Fast 4초 1/4 바퀴 왕복) | `visual_real`과 같음 | `*_video.mp4`가 9:16 회전 영상, + 실사 사진 `*_realize_fit_frame.png`·자세 사진 `*_realize_pose.png`, 검증은 피팅룸 검사 | 약 5분 [추정: 실사화 약 2분 10초 + 몸·렌더 약 1분 + 착장 약 1.7분(분석 제외, 11-22 기록의 export 9초 + Blender 94초), 끝까지 돌린 기록 없음] | 실사화 1회 $0.70. `--consent --yes`가 있어야 돈다 |

```powershell
python -m tools.run_fitting_once                                   # 기본 어댑터, 실사화 미동의(렌더까지)
python -m tools.run_fitting_once --adapters fake --consent
python -m tools.run_fitting_once --body 175,100,80,40,56 --garment-id tee_regular_m_navy_v1 --out C:\dev\_runs\run1
python -m tools.run_fitting_once --adapters visual_real --consent --yes   # fal 비용 발생
python -m tools.run_fitting_once --adapters visual_fitroom --consent --yes   # fal 비용 $0.70
```

- `--consent` 없이 돌리면 실사화·검증은 하지 않고 렌더까지입니다(실사화 미동의). 실제 실사화를 `--consent`로 돌리려면 `--yes`가 있어야 합니다. 없으면 아무것도 실행하지 않고 안내만 합니다.
- 단계가 바뀔 때마다 한 줄씩 출력하고, 끝나면 요약(핏 판정, 단계별 상태, 산출물 파일 위치와 있는지)을 출력해 결과 폴더(`artifacts/runs/{job_id}/`)의 `request.json`·`report.json`·`status.json`·`summary.txt`에도 씁니다. 산출물은 그 안의 `artifacts/`, 중간 파일은 `FIT_WORK_DIR`(기본 `artifacts/work/{job_id}/`)에 생깁니다.
- 종료 코드는 0 성공, 3 영상 경로 실패(핏 리포트는 있음), 2 작업 실패, 1 설정·입력 오류입니다.
- 어댑터별 환경변수·실패 코드는 [docs/adapters.md](docs/adapters.md), 몸은 [docs/body-fit.md](docs/body-fit.md), 실사화는 [docs/realize.md](docs/realize.md)(피팅룸 실사화는 [docs/realize-fitroom.md](docs/realize-fitroom.md)), 검증은 [docs/visual-check.md](docs/visual-check.md)에 있습니다.
- 견본 검색은 `python -m tools.search_sample --garment data/garments/tee_regular_m_navy_v1.json --samples tests/fixtures/catalog/samples_v0.json`으로 확인합니다([견본 검색](docs/api/README.md)).

## 서버 실행

```bash
uvicorn server.app:app --reload   # http://127.0.0.1:8000/docs 에서 OpenAPI 문서
```

핏 API(`/api/fit`)와 피팅 작업 API(`/api/fittings`, `/api/artifacts`)가 함께 뜹니다. 핏 계산기는 항상 실제이고, 영상 단계(몸·착장·렌더·실사화·검증)는 [어댑터 설정](docs/adapters.md)대로 가짜 또는 실제 어댑터로 돕니다(지금 기본은 전부 가짜). 작업 상태는 메모리에만 있어 서버를 다시 켜면 사라집니다. 경로와 상태는 [docs/api/README.md](docs/api/README.md)에 있습니다.

| 환경변수 | 기본 | 뜻 |
|---|---|---|
| `FIT_ADAPTERS` | `default` | 어댑터 설정: `config/adapters/`의 이름(`default`, `fake`)이나 yaml 파일 경로 |
| `FIT_WORK_DIR` | `artifacts/work/` | 어댑터의 중간 파일(.blend, 메시 등) 폴더 |
| `FIT_ARTIFACTS_DIR` | `artifacts/files/` | 산출물 폴더 |
| `FIT_MAX_WORKERS` | 1 | 동시에 도는 영상 경로 수 |
| `FIT_PIPELINE_CONFIG` | `config/pipeline_v0.yaml` | 실패 처리 설정(단계별 시간 제한·감시 시간·같은 ID 재조회 횟수·간격). 파일이 틀리면 서버·`run_fitting_once`가 시작하지 않는다 |
| `FIT_FAKE_STAGE_DELAY_S` | 0 | 가짜 영상 단계마다 쉬는 초 (화면에서 진행 상태를 볼 때). 영상 포트가 모두 가짜일 때만 |
| `FIT_FAKE_PLACEHOLDERS` | 꺼짐 (`1`이면 켬) | 가짜 모드에서 이미지 산출물에 단색 자리표시 PNG를 쓴다. 영상 포트가 모두 가짜일 때만 |
| `FIT_GARMENTS_DIR` | `data/garments/` | 등록 의류 폴더 |
| `FIT_ENV_FILE` | 저장소 맨 위 `.env` | 시작할 때 읽을 `.env`. `off`면 읽지 않는다(설치 3단계) |

```bash
FIT_FAKE_STAGE_DELAY_S=1.5 FIT_FAKE_PLACEHOLDERS=1 uvicorn server.app:app --port 8000
```

### 어댑터 바꾸기

영상 단계 5개를 가짜 또는 실제 어댑터로 고르는 설정은 `config/adapters/*.yaml`입니다(`default.yaml`이 기본, `fake.yaml`은 늘 전부 가짜). 포트 하나당 `fake` 또는 `"파이썬.모듈:팩토리"` 한 줄이라, 담당자는 자기 어댑터가 머지될 때 그 줄만 바꿉니다. **설정이 틀리면 가짜로 대신하지 않고 서버가 뜨지 않습니다.**

```bash
FIT_ADAPTERS=fake uvicorn server.app:app --port 8000          # config/adapters/fake.yaml
FIT_ADAPTERS=/내/경로/mixed.yaml uvicorn server.app:app       # 일부만 실제로 섞은 내 설정
```

팩토리 약속·산출물 파일 이름·연결 방법은 [docs/adapters.md](docs/adapters.md)에 있습니다. 필요한 환경변수는 `.env.example`(`BLENDER_PATH`, `SMPL_MODEL_DIR`, `FAL_KEY` 등)에 있고, 빠진 것은 `python -m tools.check_env --adapters <설정>`으로 봅니다.

## 팀 공유 드라이브

`.blend`·영상·메시·SMPL 모델처럼 커밋하지 않는 파일의 위치: (첫 공유 때 적는다)

## 이전 저장소

이 저장소 이전의 작업 기록은 [aix-2026f-team10_Fit_Ruler](https://github.com/junyoung0423/aix-2026f-team10_Fit_Ruler) (보관됨)에 있습니다. 공통 계약·핏 엔진(B)과 GarmentCode→Blender 착장 스파이크(C)는 이 저장소로 계획 순서에 맞춰 PR로 다시 옮깁니다.

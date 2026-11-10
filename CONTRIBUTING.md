# 협업 규칙

이 저장소의 규칙은 팀의 3주 개발 계획(`aimax-3week-plan.md`)을 따른다. 이 문서는 그중 매일 쓰는 부분만 모았다.

## 이슈 번호

GitHub 이슈 #1~#13은 활동지의 이슈 번호와 같다. 모든 브랜치·커밋·PR에 이슈 번호를 붙인다.

| # | 이슈 | 담당 |
|---|---|---|
| 1 | 공통 데이터·측정·인터페이스 기준 | 전원 (B 주도) |
| 2 | 핏 판정 계산기 | B |
| 3 | AI Hub 티셔츠 데이터 등록 | C (A 후보 선별) |
| 4 | 티셔츠 패턴 견본집 생성 | C |
| 5 | 치수가 비슷한 견본 검색 | B |
| 6 | 견본 최적화 | C |
| 7 | 내 치수로 3D 몸 만들기 | D |
| 8 | Blender 착장 | C (옷·원단) · D (몸·충돌 점검) |
| 9 | 피팅 입력·결과 화면 | A |
| 10 | 정면 렌더 클립·실사화 | C (렌더) · D (실사화) |
| 11 | 대표 프레임·색·실루엣 검증 | D · C |
| 12 | 실패해도 핏 결과 제공 | B · A |
| 13 | 통합 실행·설치 안내 | 전원 |

## 브랜치·커밋·PR

1. **브랜치**: `종류/이슈번호-요약`. 예: `feat/2-fit-api`. 종류는 `feat`·`fix`·`docs`·`spike`·`chore`.
2. **커밋**: 이슈 번호로 시작한다. 예: `#2 가슴 여유량 계산 추가`.
3. **PR 제목**: `[계획 PR 번호][#이슈] 요약`. 예: `[B2][#2] 핏 API`. 본문에는 이슈를 끝내는 PR이면 `Closes #2`, 중간 PR이면 `Refs #2`.
4. **크기**: PR 하나에 결과물 하나. 작업을 시작하면 바로 Draft PR을 열고, 다 되면 Ready for review로 바꾼다.
5. **쌓는 법**: 브랜치는 항상 최신 `main`에서 만든다. 남의 머지 안 된 브랜치 위에 쌓지 않는다. Squash merge라서 아래 브랜치가 머지되면 위 브랜치에 같은 변경이 두 번 남아 충돌이 난다. 아직 없는 남의 결과물은 가짜 어댑터·예시 JSON·공유 드라이브 파일로 대신한다.
6. **따라가기**: main이 바뀌면 `git fetch origin` 후 `git rebase origin/main`. 충돌은 작성자가 푼다.

## 리뷰와 머지

- 리뷰어는 그 결과물을 받아 쓰는 사람이다. 계획의 PR 표에 적힌 리뷰어를 지정한다.
- 리뷰어는 요청을 받으면 24시간 안에 본다. 집중 작업일에는 몇 시간 안에 본다.
- 리뷰어 승인 + CI 통과 후 **작성자가** Squash merge하고 브랜치를 지운다. 관리자 우회(bypass)는 쓰지 않는다.
- 각자 자기 작업은 자기가 PR로 올린다. PR 작성자가 곧 기여 기록이다.

## 공통 계약

`fitting/domain/`·`fitting/ports/`를 바꾸는 PR은 단톡방에 먼저 알리고, B와 그 계약을 쓰는 사람이 모두 리뷰한다. 필드를 지우거나 이름을 바꾸지 않고, 기본값 있는 필드를 추가한다.

## 폴더와 담당

| 폴더 | 담당 | 내용 |
|---|---|---|
| `web/` | A | 입력·결과·관리 화면 (A1에서 만든다) |
| `server/` | B | FastAPI: 핏 API, 피팅 작업·상태 API |
| `fitting/domain/`, `fitting/ports/` | B (전원 리뷰) | 공통 스키마·상태값, 포트 인터페이스 |
| `fitting/fit/` | B | 핏 판정 계산기 #2 |
| `fitting/services/` | B | 피팅·의류 준비 서비스, 작업 실행기, 실패 처리 |
| `fitting/catalog/` | B · C | `search.py` #5 (B), `sample_plan.py` #4·`optimize.py` #6 (C) |
| `fitting/body/`, `fitting/verify/` | D | 몸 최적화 #7, 착장 점검·색·실루엣 판정 #8 #11 |
| `fitting/adapters/fake/` | B | 가짜 어댑터 (테스트·CI용) |
| `fitting/adapters/aihub·garmentcode·blender/` | C | |
| `fitting/adapters/smpl·fal_ltx·media·meshcheck/` | D | |
| `tools/` | C · B | 견본집 생성·의류 등록(C), 피팅 1회 실행(B) |
| `config/` | B | 핏 기준표·허용 오차·검증 기준 (YAML) |
| `data/` | 전원 | 작은 메타데이터만 커밋. 원본은 `data/raw/` (커밋 안 됨) |
| `docs/` | 전원 | `decisions.md`, `spikes/`, `ux/` |

로직 코드(`fitting/` 중 `adapters/` 밖)는 bpy·smplx·fal_client나 `adapters/`를 직접 import하지 않는다. 외부 도구는 포트를 통해서만 부른다.

Blender·GarmentCode처럼 별도 Python 환경이 필요한 코드는 어댑터가 하위 프로세스로 실행한다. 필요한 환경은 그 어댑터 폴더의 README에 적는다.

## 테스트와 CI

- CI는 `ruff check .`와 `pytest`를 Python 3.10·3.12에서 돌린다.
- Blender·SMPL·fal이 필요한 테스트에는 `@pytest.mark.blender`·`smpl`·`fal`을 붙인다. CI에서는 빠지고, 작성자가 로컬 실행 결과를 PR 본문에 붙인다.
- fal은 비용이 들어 CI와 테스트에서 호출하지 않는다.

## 커밋 금지

`.env`(`FAL_KEY`), `models/`(SMPL 모델은 재배포 불가), AI Hub 원본, `artifacts/`(`.blend`·영상·메시). 큰 결과물은 팀 공유 드라이브에 두고 위치를 README에 적는다.

## 기록

- 기준을 정하면 `docs/decisions.md`에 날짜·결정·이유를 한 줄씩 남긴다.
- AI를 쓴 PR은 같은 PR에서 `PROMPTS.md`에 무엇을 어떻게 썼는지 추가한다.
- 매일 단톡방에 세 줄 체크인: 한 일, 할 일, 막힌 것.

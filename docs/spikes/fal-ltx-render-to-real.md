# 스파이크: fal / LTX-2.3 render-to-real (D1)

작성 2026-11-10 · 관련 이슈 #10 #11 · 상태: 호출 코드와 드라이런 완료, **실제 1회 호출 전**

렌더 클립 하나를 실사 영상으로 바꾸는 호출이 실제로 되는지 확인한다. 호출·저장·대표 프레임 추출까지 해 보고, 실제 설정·요청·오류·1회 비용과 시간을 남겨 D4(fal 어댑터)와 D5(파이프라인)의 기준으로 쓴다.

## 1. 확인한 사실

2026-11-10에 fal 모델 페이지([API 문서](https://fal.ai/models/fal-ai/ltx-2.3-quality/render-to-real/api), [모델 페이지](https://fal.ai/models/fal-ai/ltx-2.3-quality/render-to-real))에서 확인했다. 바뀔 수 있으니 호출 전에 다시 본다.

| 항목 | 내용 |
|---|---|
| 엔드포인트 | `fal-ai/ltx-2.3-quality/render-to-real` (render-to-real은 이 하나뿐) |
| 'Light 설정' | 별도 엔드포인트가 아니라 입력 `intensity`의 값 `light`(스키마 기본값). 다른 값은 `strong`, `strong-v2` |
| 필수 입력 | `video_url` 하나 |
| 주요 선택 입력 | `prompt`(트리거 `3DREAL.`로 시작), `resolution`(`480p`·`720p`, 짧은 변 기준·비율 유지, 기본 720p), `num_frames`(기본 121), `frames_per_second`(기본 24), `num_inference_steps`(기본 15, 최대 30), `guidance_scale`(기본 1), `generate_audio`(기본 켜짐), `seed`, `negative_prompt`, `enable_detail_refine`(가로세로 2배, 가격 약 4배), `video_quality`, `image_url`(첫 프레임 기준 이미지) |
| 출력 | `video { url, content_type, file_name, file_size }`, `seed`, `prompt`. 크기·fps·프레임 수는 없어서 내려받은 파일에서 잰다 |
| 가격 | 생성 영상 1메가픽셀당 $0.0024075. 메가픽셀 = 너비 × 높이 × 프레임 수 ÷ 1,000,000, 올림. 예: 1280×720·121프레임 → 112MP → $0.2696 |
| 길이 상한 | 720p 약 6초, 480p 약 15초. 넘으면 422로 거절 |
| 입력 형식 | mp4, mov, webm, m4v, gif |
| 키 | 환경변수 `FAL_KEY` |
| 클라이언트 | 문서에는 JavaScript 예시만 있다. Python은 `fal-client` 1.0.3의 `upload_file`, `subscribe(with_logs, on_enqueue, on_queue_update, client_timeout)`를 쓴다 |

**문서와 다른 점**: 스키마의 `intensity` 기본값은 `light`인데, 모델 페이지 플레이그라운드에는 `strong-v2`가 선택돼 있다. 그래서 `light`를 직접 지정해서 보낸다.

## 2. 1회 호출 설정

| 입력 | 값 | 이유 |
|---|---|---|
| `intensity` | `light` | 계획서의 'Light 설정 우선' |
| `resolution` | `720p` | 검증(D6)에 쓸 만한 크기. 비용을 줄이려면 `480p` |
| `num_frames` | 입력 길이를 넘지 않는 8k + 1 (3초 24fps → 65) | LTX 계열은 '8의 배수 + 1' 프레임에서 맞게 돈다는 가정(fal 기본값 121 = 8 × 15 + 1). 1회 호출로 확인할 항목 |
| `frames_per_second` | 24 | fal 기본값 |
| `num_inference_steps`, `guidance_scale` | 15, 1 | fal 기본값 |
| `generate_audio` | `false` | 소리가 필요 없다(계획서 D4: 오디오 끔) |
| `seed` | 42 | 다시 돌렸을 때 비교하려고 고정 |
| `negative_prompt` | 보내지 않음 | fal 기본값 사용 |
| `enable_detail_refine` | `false` | 비용 약 4배 |
| `prompt` | 아래 | 옷 색·원단을 적고, 렌더의 색과 모양을 유지하라고 적는다 |

```text
3DREAL. A realistic photo of a person wearing a plain black short-sleeve crew-neck cotton T-shirt,
front view from the neck down, plain light gray studio background, soft even lighting, natural fabric folds.
Keep the garment color and shape exactly as in the render.
```

옷 설명은 `--garment`로 바꿀 수 있다. 의류별 프롬프트 템플릿은 D5에서 만든다.

## 3. 예상 비용

| 입력 | 설정 | 예상 출력 | 메가픽셀 | 예상 비용 |
|---|---|---|---|---|
| 임시 클립 540×720, 3초 24fps | 720p, 65프레임 | 720×960 | 45 | $0.1083 |
| 임시 클립 540×720, 3초 24fps | 480p, 65프레임 | 480×640 | 20 | $0.0481 |
| 1280×720 렌더 3초 (C의 클립 가정) | 720p, 65프레임 | 1280×720 | 60 | $0.1445 |
| 참고: fal 페이지 예시 | 720p, 121프레임 | 1280×720 | 112 | $0.2696 |

임시 클립 두 줄은 드라이런으로 계산했다. 실제 청구액은 fal 대시보드에서 확인해 5절에 적는다.

## 4. 실행 방법

```bash
python -m venv .venv
source .venv/Scripts/activate        # PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements-fal-spike.txt
```

```bash
python -m tools.spike_fal_ltx --make-test-clip            # 드라이런 (임시 클립)
python -m tools.spike_fal_ltx --input path/to/render.mp4  # 드라이런 (C의 렌더 클립)
python -m tools.spike_fal_ltx --input path/to/render.mp4 --yes   # 실제 1회 호출, 비용 발생
python -m pytest tests/adapters                           # 가짜 클라이언트 테스트
```

- **키**: `FAL_KEY`는 환경변수나 저장소 맨 위 `.env` 파일에 `FAL_KEY=...` 한 줄로 둔다. `.env`는 커밋하지 않는다.
- **안전장치**
  - 기본은 드라이런이다. `--yes`를 붙여야 호출한다.
  - 예상 비용이 `--max-cost-usd`(기본 $0.5)를 넘으면 호출하지 않는다.
  - 출력 길이가 해상도별 상한을 넘으면 호출 전에 멈춘다.
  - 테스트와 CI는 가짜 클라이언트만 쓴다.
- **글자 깨짐**: Git Bash에서 한글 출력이 깨지면 `PYTHONUTF8=1`을 붙여 실행한다.

결과는 `artifacts/spikes/fal_ltx/<시각>/`에 남는다. 이 폴더는 커밋하지 않는다.

| 파일 | 내용 |
|---|---|
| `input*.mp4` | 입력 클립 사본 또는 임시 클립 |
| `frame_render.png` | 입력 클립의 대표 프레임 |
| `plan.json` | 입력 정보, 보낼 설정, 예상 비용 (드라이런에도 생김) |
| `request.json` | 실제로 보낸 요청 |
| `response.json` | fal 응답 |
| `output.mp4` | 실사 영상 |
| `frame_real.png` | 실사 영상의 대표 프레임 |
| `record.json` | 위 내용 + request_id, 단계별 시간, 출력 크기, 출력 기준 비용 |
| `error.json` | 실패했을 때만 생김: 실패 단계, 오류 종류, 상태 코드, 응답 본문 |

## 5. 대표 프레임 추출

`fitting/adapters/media/frames.py`

- 앞뒤 20%를 뺀 구간에서 가장 선명한 프레임을 고른다. 선명도는 라플라시안 분산으로 잰다. 생성 영상은 처음과 끝이 흔들리거나 흐린 경우가 많아서 그 구간을 뺀다.
- 가운데 프레임을 고르는 방식(`method="middle"`)도 있다.
- 영상은 한 프레임씩 읽어 메모리를 아낀다. 프레임 수는 컨테이너에 적힌 값 대신 실제로 읽어서 센다.
- 렌더 클립과 실사 결과에서 각각 뽑는다. 두 영상의 대응 프레임 매칭(길이·fps 차이 보정)은 D5에서 한다.

## 6. 실제 1회 호출 결과

아직 호출하지 않았다. 호출하면 `record.json`과 fal 대시보드를 보고 아래를 채운다.

| 항목 | 결과 |
|---|---|
| 날짜 | |
| 입력 클립 | (C의 렌더 클립 / 임시 클립) 크기·fps·길이 |
| request_id | |
| 실제로 보낸 설정 | `request.json` 요약 |
| 출력 | 크기, fps, 프레임 수 (요청한 프레임 수와 같은지) |
| 시간 | 업로드 / 대기 / 생성 / 전체 (초) |
| 비용 | 출력 기준 계산 $ / 대시보드 청구 $ |
| 오류 | 없음 또는 `error.json` 요약 |
| 관찰 | 옷 색 유지, 실루엣 유지, 얼굴이 프레임 밖에 그대로 있는지, 깜빡임 |

## 7. D4·D5로 넘길 것

- **기본 설정**: 오디오 끔, 프레임 수 8k + 1, 해상도별 길이 상한 검사, 예상 비용 상한, 대기 시간 상한(`client_timeout`)
- **기록 형식**: `request.json` / `response.json` / `error.json`의 형식. D4 어댑터의 '요청·오류·비용 기록'에 그대로 쓴다.
- **어댑터 구조**: 정식 어댑터는 B의 포트(`fitting/ports/`)가 나오면 D4에서 그 모양에 맞춘다. 재시도 1회와 가짜 어댑터 테스트도 D4에서 붙인다.
- **외부 전송**: 렌더 클립이 fal 서버로 전송된다. A5의 동의 문구("신체가 반영된 렌더 클립이 fal 서버에서 처리됨")와 맞춘다.
- **이용 조건**: LTX-2.3 모델 라이선스와 fal 이용 약관은 최종 점검표 항목으로 따로 확인한다.

## 8. 열린 질문

- C의 실제 렌더 클립 크기와 fps는 얼마인가? 비용과 프레임 수가 여기에 따라 달라진다.
- `light`와 `strong-v2`의 품질 차이는 어떤가? 비교에 비용이 들므로 1회 결과를 보고 정한다.
- 출력 프레임 수가 요청한 값과 같은가? 8k + 1 가정을 1회 호출로 확인한다.
- 임시 클립은 평면 도형이라 실사화 품질을 판단하는 데는 맞지 않는다. 품질 판단은 C의 3D 렌더 클립으로 한다.

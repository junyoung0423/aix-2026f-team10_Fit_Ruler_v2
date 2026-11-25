"""영상 읽기·프레임 추출·색/실루엣 측정 어댑터 (OpenCV).

- `frames.py`: 영상 정보, 대표 프레임(D1), 대응 렌더 프레임 저장(D5), 여러 프레임 읽기(D6)
- `visual_measure.py`: 11프레임 색·실루엣 측정, 실사 옷 영역 찾기, 접촉 시트(D6)
- `validator.py`: `Validator` 포트(D5 프레임 + D6 판정). 팩토리는 `factory.py`
"""

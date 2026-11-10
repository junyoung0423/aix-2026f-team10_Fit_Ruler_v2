def pytest_configure(config):
    config.addinivalue_line(
        "markers", "smpl: SMPL 모델 파일이 있어야 도는 테스트 (CI에서 뺀다)"
    )

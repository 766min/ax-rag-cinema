"""pytest 가 이 파일을 읽으면서 tests/ 를 import 경로에 넣는다 (rootdir conftest, prepend 모드).
그래서 테스트에서 `from fakes import FakeLLM` 처럼 공용 가짜 부품을 가져올 수 있다."""

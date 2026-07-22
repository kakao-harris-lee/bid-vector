"""도메인 값 타입·순수 규칙 모듈.

경계(라우터·task·predictor)에서 금액 basis를 명시하고, 반복 base/basis 대입
버그를 타입/단일함수로 차단하기 위한 순수 코어. IO 0.

- :mod:`app.domain.money` — 금액 basis 타입(Basis enum, BaseAmount/YegaAmount).
- :mod:`app.domain.basis_conversion` — 예정가↔기초금액 변환 단일 출처.

이 두 모듈은 저장소의 첫 mypy strict 아일랜드다(pyproject.toml ratchet).
"""

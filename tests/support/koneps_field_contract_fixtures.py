"""KONEPS 필드 계약 테스트 공용 픽스처 — 교정 전(레거시) base 해석 순서.

``BASE_BASIS_PRECEDENCE`` 위반은 현재 순서(기초금액 키 우선)에서는 발생할 수 없고,
순서가 예산·예정가 우선으로 되돌아갈 때만 발화하는 드리프트 감지기다. 그 성질을 고정하는
테스트가 검증기·관찰기·검증 스크립트 3개 파일에 있어 같은 레거시 순서 튜플이 3벌로
복사됐다(§4.5-8 중복 금지). 여기서 단일 정의한다.

키 집합을 다시 적지 않고 **현재 선언에서 파생**한다: 기초금액 키를 뒤로 보내기만 하므로,
``field_contract_spec`` 에 후보 키가 추가돼도 이 픽스처는 자동으로 따라간다(같은 키 집합,
예정가·예산 우선). 정렬은 안정적이라 그룹 내부 순서도 선언 그대로 보존된다.
"""

from __future__ import annotations

from app.services.koneps import field_contract_spec as fcs

LEGACY_YEGA_FIRST_BASE_ORDER: tuple[str, ...] = tuple(
    key for key in fcs.BASE_RESOLUTION_ORDER if key not in fcs.TRUE_BASE_KEYS
) + tuple(fcs.TRUE_BASE_KEYS)

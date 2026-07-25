"""라이브 KONEPS 수집 경로가 ``field_contract`` 순수 검증기를 **관찰 전용**으로 소비하는
얇은 run-단위 집계기.

배경(#227): ``field_contract`` 의 순수 검증기(``validate_item`` 등)는 신규 수집 필드를
소비 전에 실배치 assert 하기 위한 게이트였지만, 프로덕션 소비자가 0이라 온-디맨드 스크립트
(``scripts/verify_koneps_field_contract.py``)에서만 돌았다. 이 모듈은 그 검증기를 라이브
수집 루프에 배선하되 **수집 동작을 바꾸지 않는다**: 행을 드롭하거나 실패시키지 않고, 위반·
미지(unknown) 필드를 run 단위로 세어 요약 1줄만 남긴다.

per-item 로그 폭주를 막기 위해 상태는 이 집계기가 들고, 로그는 호출부(collection)가
``summary_line`` 한 줄로 남긴다. IO/DB 없음 — 순수 카운터라 테스트가 값 검증으로 끝난다
(§4.7). #228 known-benign(배정예산 base 해석 WARN)은 ``field_contract.is_known_benign``
선언 데이터로 판별해 별도 버킷(``benign_suppressed``)으로 분리한다 — 요약 노이즈 억제일
뿐 계약 의미는 바꾸지 않는다.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.services.koneps.field_contract import (
    Severity,
    is_known_benign,
    unknown_fields,
    validate_item,
)


@dataclass
class FieldContractObservation:
    """한 수집 run 의 계약 관찰 집계(순수 카운터 — IO 없음).

    ``observe`` 로 raw item 을 하나씩 흘려보내면 순수 검증기가 낸 위반/미지 필드를 종류별로
    센다. known-benign 위반은 ``benign_suppressed`` 로 분리해 ``errors``/``warns`` 를 부풀리지
    않는다. 어떤 경우에도 입력 item 을 변경하지 않는다.
    """

    items_checked: int = 0
    errors: int = 0
    warns: int = 0
    violations: Counter[str] = field(default_factory=Counter)
    unknown: Counter[str] = field(default_factory=Counter)
    benign_suppressed: Counter[str] = field(default_factory=Counter)

    def observe(self, item: Mapping[str, object], *, operation: str | None) -> None:
        """raw item 하나를 순수 검증기에 통과시켜 위반/미지 필드를 집계한다(관찰 전용)."""
        self.items_checked += 1
        for violation in validate_item(item, operation=operation):
            if is_known_benign(violation):
                self.benign_suppressed[violation.kind.value] += 1
                continue
            self.violations[violation.kind.value] += 1
            if violation.severity is Severity.ERROR:
                self.errors += 1
            else:
                self.warns += 1
        for name in unknown_fields(item):
            self.unknown[name] += 1

    @property
    def has_findings(self) -> bool:
        """억제되지 않은 위반이나 미지 필드가 하나라도 있으면 True(요약 로그 트리거)."""
        return bool(self.violations or self.unknown)

    def summary(self) -> dict[str, Any]:
        """run 집계를 수집 metadata 에 실을 순수 dict 로 렌더한다(시크릿 없음)."""
        return {
            "items_checked": self.items_checked,
            "errors": self.errors,
            "warns": self.warns,
            "violations": dict(self.violations),
            "unknown_fields": dict(self.unknown),
            "benign_suppressed": dict(self.benign_suppressed),
        }

    def summary_line(self) -> str:
        """사람이 읽는 요약 한 줄(로그용, 시크릿 없음). per-item 이 아니라 run 단위."""
        return (
            "[field-contract] 라이브 관찰(수집 불변): "
            f"items={self.items_checked} errors={self.errors} warns={self.warns} "
            f"violations={dict(self.violations)} "
            f"unknown_fields={dict(self.unknown)} "
            f"benign_suppressed={dict(self.benign_suppressed)}"
        )

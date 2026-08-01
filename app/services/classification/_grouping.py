"""면허 그룹(``lmtGrpNo``) 매핑·표시 해석기 — ``classification`` 패키지 내부 전용.

밑줄 프리픽스는 **패키지 내부 계약**이라는 뜻이다. 패키지 밖에서 import 하지 않는다.

협회 가입(:mod:`association`) 축과 기술부문(:mod:`tech_field`) 축은 도메인 어휘만
다른 **같은** 그룹핑·표시 알고리즘을 쓴다. CLAUDE.md §4.5-8 처방대로 알고리즘은
여기 한 벌만 두고, 각 축은 자기 매처를 넘기는 얇은 명명 래퍼로 도메인 이름과 근거를
지킨다. 새 축(예: 또 다른 cohort 자격)이 생겨도 해석기를 복사하지 않는다.

**경계**: 이 모듈은 그룹 경계를 존중한 *매핑*과 *표시*만 해석한다. 그룹 의미론
(그룹 간 OR·그룹 내 AND) 판정은 :func:`app.services.classification.group_or.
evaluate_group_or` 가, 어휘 매칭 규칙은 :mod:`app.services.eligibility_labeling`
가, 그룹핑 자체는 :func:`app.services.license_eligibility.parse_license_limit_groups`
가 소유한다. 그 셋을 여기로 옮기지 않는다(단일 출처 유지).
"""

from __future__ import annotations

from collections.abc import Callable

from app.services.license_eligibility import parse_license_limit_groups

# 자유 텍스트(면허명) → canonical 용어 집합. ``eligibility_labeling`` 의
# ``match_association_terms`` · ``match_tech_field_terms`` 가 이 형태다.
TermMatcher = Callable[[str], frozenset[str]]


def terms_by_license_group(
    eligibility_raw: dict | None, match_terms: TermMatcher
) -> list[frozenset[str]]:
    """공고 면허요건을 ``lmtGrpNo`` 그룹별 canonical 용어 집합으로 매핑한다(순수).

    면허 게이트와 **동일한 그룹핑 단일 출처**(``parse_license_limit_groups``)로 행을
    lmtGrpNo 그룹으로 묶고(그룹 간 OR·그룹 내 AND), 각 그룹의 요구 면허명
    (``.names()``)을 ``match_terms`` 로 canonical 집합에 union 한다. 그룹 등장 순서를
    유지하며, ``match_terms`` 어휘를 하나도 요구하지 않는 그룹은 **빈 frozenset** 으로
    남긴다 — 호출부가 이를 "무제약 자격 경로"로 인지해 과차단하지 않도록 하기 위한
    계약이라, 빈 그룹을 걸러내면 안 된다(license-axis 커버리지 비대칭 교훈).

    반환 집합의 값 공간은 전적으로 ``match_terms`` 가 정한다 — 이 해석기는 무엇을
    매칭하는지 알지 못한다. IO/DB 접근 없음.
    """
    groups: list[frozenset[str]] = []
    for group in parse_license_limit_groups(eligibility_raw):
        terms: set[str] = set()
        for name in group.names():
            terms |= match_terms(name)
        groups.append(frozenset(terms))
    return groups


def format_groups(groups: list[frozenset[str]]) -> str:
    """요건 그룹 목록을 사람이 읽는 ``[a, b] / [c]`` 형태로 만든다(reason 용).

    그룹 **안**은 정렬해 결정적으로 출력하고(집합은 순서가 불안정해 정렬 없이는
    reason 문구가 실행마다 흔들린다), 그룹 **사이**는 입력 순서를 그대로 둔다
    (등장 순서 = 공고의 lmtGrpNo 순서). 빈 그룹은 ``[]`` 로 나타난다.

    도메인 어휘를 모르므로 협회 요건과 기술부문 요건에 그대로 쓰인다. 호출부는
    "하나만 전부 보유하면 됨" 같은 그룹 간 OR 설명을 자기 문장으로 덧붙인다.
    """
    return " / ".join(f"[{', '.join(sorted(group))}]" for group in groups)

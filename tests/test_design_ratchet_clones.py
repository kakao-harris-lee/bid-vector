"""중복 헬퍼 클론 탐지 모듈의 판정 계약.

이 테스트는 predicate 를 **양방향으로** 고정한다. 배제 목록이나 임계값을 손대면 여기가
깨지므로 튜닝이 조용히 드리프트하지 못한다.
"""

from __future__ import annotations

import ast

from scripts._design_ratchet_clones import (
    CLONE_MIN_AST_NODES,
    collect_clone_signatures,
)

# G01 실사례의 축약형: 선언된 키 집합으로 dict 를 투영하고 빈 값은 버린다.
PROJECT_DECLARED_KEYS = """
def extract_eligibility_flags(raw_item):
    picked = {}
    for key in ELIGIBILITY_RAW_KEYS:
        value = raw_item.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            picked[key] = text
    return picked or None
"""

# 같은 알고리즘, 이름과 상수만 다름 → 같은 지문이어야 한다.
PROJECT_DECLARED_KEYS_RENAMED = """
def _project_license_limit_item(row):
    projected = {}
    for field in LICENSE_LIMIT_ITEM_KEYS:
        raw = row.get(field)
        if raw is None:
            continue
        cleaned = str(raw).strip()
        if cleaned:
            projected[field] = cleaned
    return projected or None
"""


def _signatures(source: str, path: str = "app/sample.py"):
    return collect_clone_signatures(path, ast.parse(source))


def _digest(source: str) -> str:
    signatures = _signatures(source)
    assert len(signatures) == 1, f"기대 1개, 실제 {len(signatures)}개"
    return signatures[0].digest


class TestMechanicalPredicate:
    def test_decorated_function_is_excluded(self) -> None:
        """얼짜 FastAPI 라우터는 구조가 같아도 통합 대상이 아니다(CLAUDE.md §4)."""
        source = PROJECT_DECLARED_KEYS.replace(
            "def extract", "@router.get('/x')\ndef extract"
        )
        assert _signatures(source) == []

    def test_db_session_access_is_excluded(self) -> None:
        source = """
def load_rows(db, limit):
    rows = db.query(Model).limit(limit).all()
    collected = []
    for row in rows:
        if row is None:
            continue
        collected.append(row)
    return collected
"""
        assert _signatures(source) == []

    def test_method_not_touching_self_is_included(self) -> None:
        """본문이 self 를 안 쓰면 메서드도 메커니컬 헬퍼다(인자 이름만으로 배제하지 않는다)."""
        source = """
class Reporter:
    def project(self, raw_item):
        picked = {}
        for key in DECLARED_KEYS:
            value = raw_item.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                picked[key] = text
        return picked or None
"""
        assert len(_signatures(source)) == 1

    def test_method_touching_self_is_excluded(self) -> None:
        source = """
class Reporter:
    def project(self, raw_item):
        picked = {}
        for key in self.declared_keys:
            value = raw_item.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                picked[key] = text
        return picked or None
"""
        assert _signatures(source) == []

    def test_tiny_function_is_below_threshold(self) -> None:
        assert _signatures("def tiny(value):\n    return value\n") == []


class TestNormalization:
    def test_same_algorithm_different_names_share_digest(self) -> None:
        assert _digest(PROJECT_DECLARED_KEYS) == _digest(PROJECT_DECLARED_KEYS_RENAMED)

    def test_different_constant_literal_splits_digest(self) -> None:
        two = """
def round_two(values):
    total = 0.0
    for item in values:
        total += float(item)
    return round(total, 2)
"""
        three = two.replace("round_two", "round_three").replace("total, 2", "total, 3")
        assert _digest(two) != _digest(three)

    def test_docstring_only_body_yields_no_signature(self) -> None:
        source = '''
def placeholder(value):
    """이 함수는 본문이 docstring 뿐이라 비교 대상이 아니다."""
'''
        assert _signatures(source) == []


class TestSignatureFields:
    def test_records_file_function_and_node_count(self) -> None:
        signature = _signatures(PROJECT_DECLARED_KEYS, "app/koneps/openapi.py")[0]
        assert signature.file == "app/koneps/openapi.py"
        assert signature.function == "extract_eligibility_flags"
        assert signature.member == "app/koneps/openapi.py:extract_eligibility_flags"
        assert signature.node_count >= CLONE_MIN_AST_NODES

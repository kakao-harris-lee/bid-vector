"""중복 헬퍼 클론 탐지 모듈의 판정 계약.

이 테스트는 predicate 를 **양방향으로** 고정한다. 배제 목록이나 임계값을 손대면 여기가
깨지므로 튜닝이 조용히 드리프트하지 못한다.
"""

from __future__ import annotations

import ast

from scripts._design_ratchet_clones import (
    CLONE_ALLOWLIST,
    CLONE_MIN_AST_NODES,
    CloneSignature,
    collect_clone_signatures,
    count_cross_file_clones,
    count_local_clones,
    cross_file_clone_groups,
    unused_allowlist_keys,
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

# app/services/synthetic_backtest.py 의 실제 소스(§7.1 "반드시 잡아야 할 것" —
# 동일 파일 내 캐스트만 다른 쌍). 합성 ``_sig(...)`` 가 아니라 진짜 소스로 고정한다.
SYNTHETIC_BACKTEST_CAST_PAIR = """
def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
"""


def _signatures(source: str, path: str = "app/sample.py"):
    return collect_clone_signatures(path, ast.parse(source))


def _digest(source: str) -> str:
    signatures = _signatures(source)
    assert len(signatures) == 1, f"기대 1개, 실제 {len(signatures)}개"
    return signatures[0].digest


class TestMechanicalPredicate:
    def test_decorated_function_is_excluded(self) -> None:
        """얇은 FastAPI 라우터는 구조가 같아도 통합 대상이 아니다(CLAUDE.md §4)."""
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

    def test_global_statement_is_excluded(self) -> None:
        """global 로 모듈 상태에 결합되면 구조가 같아도 후보가 아니다(양방향 고정)."""
        control = """
def accumulate(values):
    running = 0
    for item in values:
        if item is None:
            continue
        running += int(item)
    if running < 0:
        running = 0
    return running
"""
        with_global = """
def accumulate(values):
    global RUNNING
    RUNNING = 0
    for item in values:
        if item is None:
            continue
        RUNNING += int(item)
    if RUNNING < 0:
        RUNNING = 0
    return RUNNING
"""
        assert len(_signatures(control)) == 1
        assert _signatures(with_global) == []

    def test_excluded_attribute_call_is_excluded(self) -> None:
        """배제 이름을 전혀 안 써도 배제 속성(.commit 등)을 호출하면 인프라 결합이다."""
        control = """
def persist(handle, rows):
    saved = []
    for row in rows:
        if row is None:
            continue
        saved.append(row)
    return saved
"""
        with_commit = """
def persist(handle, rows):
    saved = []
    for row in rows:
        if row is None:
            continue
        saved.append(row)
    handle.commit()
    return saved
"""
        assert len(_signatures(control)) == 1
        assert _signatures(with_commit) == []

    def test_thin_io_wrapper_is_included(self) -> None:
        """open/json/Path 를 쓰는 얇은 I/O 래퍼는 통합 대상이라 후보에 남는다."""
        source = """
def _write_json(path, payload):
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return Path(path)
"""
        assert len(_signatures(source)) == 1


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

    def test_real_source_cast_pair_shares_digest(self) -> None:
        """§7.1 "반드시 잡아야 할 것" — 실제 소스의 캐스트만 다른 동일파일 쌍."""
        signatures = _signatures(
            SYNTHETIC_BACKTEST_CAST_PAIR, "app/services/synthetic_backtest.py"
        )
        assert [signature.function for signature in signatures] == [
            "_int_or_none",
            "_float_or_none",
        ]
        assert signatures[0].digest == signatures[1].digest
        assert count_local_clones(signatures) == 2


class TestThinDelegatorExclusion:
    """§4.5-8 처방("파라미터화 + 얇은 명명 래퍼")의 산출물은 계수되면 안 된다.

    동시에 배제 정의가 **너무 넓어도** 안 된다 — 한 줄로 쓴 진짜 복붙까지 죽는다.
    두 방향을 함께 고정한다.
    """

    def test_annotated_thin_delegator_is_excluded(self) -> None:
        """처방(파라미터화 + 얇은 명명 래퍼)의 결과물이 계수되면 처방이 성립하지 않는다."""
        source = '''
def _int_or_none(value: Any) -> int | None:
    """정수로 강제하거나 None."""
    return _cast_or_none(value, int)
'''
        assert _signatures(source) == []

    def test_annotated_method_delegator_is_excluded(self) -> None:
        """타입힌트·docstring 때문에 **원본** 노드 수가 임계값을 넘어도 배제된다.

        이 케이스가 C1 의 실제 오탐이었다. 원본 29노드 · 정규화 12노드.
        """
        source = '''
class Reporter:
    def _average(self, values: list[int | float]) -> float | None:
        """Return a rounded average while preserving empty sets."""
        return average(values, digits=4)
'''
        assert sum(1 for _ in ast.walk(ast.parse(source))) > CLONE_MIN_AST_NODES
        assert _signatures(source) == []

    def test_attribute_callee_one_liner_is_kept(self) -> None:
        """호출 대상이 ``Attribute`` 면 위임이 아니라 로직이다(한 줄 복붙은 잡아야 한다)."""
        source = '''
def _normalize_key(value):
    """공백 접기 + 소문자."""
    return "".join(str(value or "").strip().lower().split())
'''
        assert len(_signatures(source)) == 1

    def test_generator_argument_one_liner_is_kept(self) -> None:
        """인자가 ``GeneratorExp`` 면 파라미터 전달이 아니다."""
        source = '''
def _split_csv(raw):
    """콤마 분해 후 공백 제거."""
    return tuple(item.strip() for item in str(raw or "").split(",") if item.strip())
'''
        assert len(_signatures(source)) == 1

    def test_nested_call_argument_delegation_is_kept(self) -> None:
        """키워드 값이 중첩 호출이면 순수 파라미터 위임이 아니다."""
        source = '''
def _band_for(t):
    """중첩 호출을 인자로 넘기는 위임은 로직이다."""
    return resolve_band(RULES, text=t, title=title_line(t), fallback=DEFAULT_BAND)
'''
        assert len(_signatures(source)) == 1

    def test_splat_delegation_is_kept(self) -> None:
        """``*``/``**`` 언패킹은 파라미터 전달이 아니라 재조립이다."""
        starred = """
def _forward(items, mode, fallback, digits):
    return combine(*items, mode, fallback, digits)
"""
        double_starred = """
def _forward(options, mode, fallback, digits):
    return combine(mode, fallback, digits, **options)
"""
        assert len(_signatures(starred)) == 1
        assert len(_signatures(double_starred)) == 1

    def test_node_count_is_measured_on_normalized_shape(self) -> None:
        """annotation·docstring 을 붙여도 노드 수가 변하지 않아야 기준이 일관된다."""
        bare = """
def _clip(text, limit):
    trimmed = str(text or "").strip()
    if len(trimmed) <= limit:
        return trimmed
    return trimmed[:limit] + "..."
"""
        annotated = '''
def _clip(text: str | None, limit: int) -> str:
    """길이를 제한하고 말줄임표를 붙인다."""
    trimmed = str(text or "").strip()
    if len(trimmed) <= limit:
        return trimmed
    return trimmed[:limit] + "..."
'''
        plain, decorated = _signatures(bare), _signatures(annotated)
        assert len(plain) == len(decorated) == 1
        assert plain[0].node_count == decorated[0].node_count
        assert plain[0].digest == decorated[0].digest


class TestSignatureFields:
    def test_records_file_function_and_node_count(self) -> None:
        signature = _signatures(PROJECT_DECLARED_KEYS, "app/koneps/openapi.py")[0]
        assert signature.file == "app/koneps/openapi.py"
        assert signature.function == "extract_eligibility_flags"
        assert signature.member == "app/koneps/openapi.py:extract_eligibility_flags"
        assert signature.node_count >= CLONE_MIN_AST_NODES


def _sig(file: str, function: str, digest: str = "aaaa") -> CloneSignature:
    return CloneSignature(
        file=file, function=function, digest=digest, node_count=42
    )


class TestCrossFileGrouping:
    def test_same_digest_across_files_is_a_group(self) -> None:
        groups = cross_file_clone_groups(
            [_sig("app/a.py", "write"), _sig("app/b.py", "dump")]
        )
        assert len(groups) == 1
        assert groups[0].members == ("app/a.py:write", "app/b.py:dump")
        assert groups[0].files == ("app/a.py", "app/b.py")

    def test_same_file_pair_is_not_a_cross_file_group(self) -> None:
        assert cross_file_clone_groups(
            [_sig("app/a.py", "int_or_none"), _sig("app/a.py", "float_or_none")]
        ) == []

    def test_unique_digest_is_not_a_group(self) -> None:
        assert cross_file_clone_groups(
            [_sig("app/a.py", "one", "aaaa"), _sig("app/b.py", "two", "bbbb")]
        ) == []

    def test_counts_are_attributed_to_every_member_file(self) -> None:
        counts = count_cross_file_clones(
            [
                _sig("app/a.py", "write"),
                _sig("app/b.py", "dump"),
                _sig("app/c.py", "save"),
            ]
        )
        assert counts == {"app/a.py": 1, "app/b.py": 1, "app/c.py": 1}

    def test_two_members_from_one_file_count_twice(self) -> None:
        counts = count_cross_file_clones(
            [
                _sig("app/a.py", "write"),
                _sig("app/a.py", "write_alt"),
                _sig("app/b.py", "dump"),
            ]
        )
        assert counts == {"app/a.py": 2, "app/b.py": 1}


class TestLocalGrouping:
    def test_same_file_clones_are_counted(self) -> None:
        assert (
            count_local_clones(
                [_sig("app/a.py", "int_or_none"), _sig("app/a.py", "float_or_none")]
            )
            == 2
        )

    def test_unique_digests_are_not_counted(self) -> None:
        assert (
            count_local_clones(
                [_sig("app/a.py", "one", "aaaa"), _sig("app/a.py", "two", "bbbb")]
            )
            == 0
        )


class TestAllowlist:
    def test_allowlisted_group_is_exempt_from_counts(self) -> None:
        key = next(iter(CLONE_ALLOWLIST))
        left, right = key.split("|")
        signatures = [
            _sig(left.rsplit(":", 1)[0], left.rsplit(":", 1)[1]),
            _sig(right.rsplit(":", 1)[0], right.rsplit(":", 1)[1]),
        ]
        assert cross_file_clone_groups(signatures) == []
        assert count_cross_file_clones(signatures) == {}

    def test_third_copy_breaks_the_exemption(self) -> None:
        """멤버 신원 키라서 복사본이 하나 더 생기면 면제가 적용되지 않는다."""
        key = next(iter(CLONE_ALLOWLIST))
        left, right = key.split("|")
        signatures = [
            _sig(left.rsplit(":", 1)[0], left.rsplit(":", 1)[1]),
            _sig(right.rsplit(":", 1)[0], right.rsplit(":", 1)[1]),
            _sig("app/newcomer.py", "copied"),
        ]
        assert len(cross_file_clone_groups(signatures)) == 1

    def test_every_allowlist_entry_has_a_reason(self) -> None:
        assert all(reason.strip() for reason in CLONE_ALLOWLIST.values())

    def test_dead_entry_is_reported(self) -> None:
        assert unused_allowlist_keys([]) == sorted(CLONE_ALLOWLIST)

    def test_live_entry_is_not_reported(self) -> None:
        key = next(iter(CLONE_ALLOWLIST))
        left, right = key.split("|")
        signatures = [
            _sig(left.rsplit(":", 1)[0], left.rsplit(":", 1)[1]),
            _sig(right.rsplit(":", 1)[0], right.rsplit(":", 1)[1]),
        ]
        assert key not in unused_allowlist_keys(signatures)

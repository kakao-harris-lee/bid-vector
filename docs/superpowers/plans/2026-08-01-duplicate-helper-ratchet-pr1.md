# 중복 헬퍼 래칫 지표 신설 (PR1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 설계 래칫에 중복 헬퍼 지표 2개(`duplicate_mechanical_helpers` ·
`duplicate_mechanical_helpers_local`)를 신설하고 현재값으로 baseline 을 동결해, 이 시점부터
새 중복이 CI 에서 차단되게 한다.

**Architecture:** 스캐너는 이미 책임 3분할이다 —
`scripts/_design_ratchet_contracts.py`(143줄, 데이터 계약 + 순수 비교) ·
`scripts/_design_ratchet_scan.py`(403줄, AST 측정) ·
`scripts/design_ratchet.py`(275줄, CLI · baseline 영속화 · 리포트). 클론 탐지 로직은
같은 패턴을 이어 `scripts/_design_ratchet_clones.py` 신규 모듈에 격리한다(403줄인 scan
모듈에 클론 탐지를 얹으면 500줄 한도를 넘겨 스캐너가 스스로 `file_loc_band` 를 올린다).
신규 필드 2개는 baseline JSON 으로 직렬화되므로 `FileMetrics` 가 있는 contracts 모듈에
추가한다. 기존 `scan_source(relative_path, source) -> FileMetrics` 순수 함수 계약은
유지하고, 교차 파일 정보가 필요한 지표만 `scan_repo` 의 2-pass 에서 채운다.

**Tech Stack:** Python 3.12 · `ast` 표준 라이브러리 · pydantic v2 (`StrictModel`) · pytest

## Global Constraints

- 대상 스펙: `docs/superpowers/specs/2026-08-01-code-duplication-consolidation-design.md`
- 작업 worktree: `/home/deploy/project/bid-vector-dup-spec`, 브랜치
  `docs/code-duplication-consolidation-spec`
- 이 worktree 는 `origin/main` **`a2b90ca`** 기준으로 rebase 되어 있다. 이 플랜의 모든
  파일·심볼 참조는 그 커밋 기준이다(PR #337~#341 의 래칫 3분할이 반영된 상태).
- 이 worktree 에는 `.venv` 가 없다. 파이썬은 **메인 체크아웃의 절대경로**로 실행한다:
  `/home/deploy/project/bid-vector/.venv/bin/python` ·
  `/home/deploy/project/bid-vector/.venv/bin/pytest` ·
  `/home/deploy/project/bid-vector/.venv/bin/ruff`
- **PR1 은 애플리케이션 코드를 통합하지 않는다.** 지표·게이트·문서만 만든다. 실제 통합은
  PR2(app/) · PR3(scripts/) 다.
- `CLONE_MIN_AST_NODES = 20`
- 신규 스캐너 모듈도 래칫 스캔 대상이므로 **위반 0** 이어야 한다: 함수 50줄 이하, 파일
  500줄 이하, `json` 직접 호출 없음, 약한 dict/Any 경계 없음.
- ruff 규칙 집합은 `["E4", "E7", "E9", "F"]`, line-length 88. mypy 는 `app/` 만 검사하므로
  `scripts/` 는 타입 검사 대상이 아니다.
- `CLONE_ALLOWLIST` 키는 **콘텐츠 해시가 아니라 멤버 신원**(`"파일:함수"` 를 정렬해
  `|` 로 결합)이다.
- allowlist 에 등재된 그룹은 지표에서 **제외**된다(카운트 0). 스펙 §7.4 의 "2그룹/4함수
  잔존"은 *코드에 잔존*(통합하지 않음)이라는 뜻이지 *지표에 잔존*이 아니다.

---

## File Structure

| 파일 | 책임 | 상태 |
|---|---|---|
| `scripts/_design_ratchet_clones.py` | 메커니컬 판정 · AST 정규화 · 클론 그룹핑 · allowlist | 신규 |
| `scripts/_design_ratchet_contracts.py` | `FileMetrics` 필드 2개 추가 | 수정 |
| `scripts/_design_ratchet_scan.py` | `scan_file` 분리 · `scan_repo_with_signatures` 2-pass · `scan_repo` 델리게이터화 | 수정 |
| `scripts/design_ratchet.py` | 죽은 allowlist 검사 · 클론 그룹 리포트 | 수정 |
| `tests/test_design_ratchet_clones.py` | 클론 모듈 단위 테스트(양방향 픽스처) | 신규 |
| `tests/test_design_ratchet.py` | 신규 지표의 scan/scan_repo 통합 테스트 | 수정 |
| `tests/design_ratchet_baseline.json` | 신규 지표 값 포함해 재생성 | 수정 |
| `.github/workflows/ci.yml` | ruff 대상에 신규 스캐너 모듈 추가 | 수정 |
| `CLAUDE.md` | §4.5-6 허브 주소표 · §4.5-8 신설 · §9 체크리스트 | 수정 |
| `docs/operations/design-ratchet.md` | 신규 지표 2개 문서화 · 미탐 범위 | 수정 |

---

### Task 1: 클론 시그니처 추출 (판정 + 정규화)

한 파일의 AST 에서 "메커니컬 헬퍼"를 골라 정규화 지문을 만든다. 그룹핑은 Task 2 다.

**Files:**
- Create: `scripts/_design_ratchet_clones.py`
- Test: `tests/test_design_ratchet_clones.py`

**Interfaces:**
- Consumes: 없음 (이 플랜의 첫 태스크)
- Produces:
  - `CloneSignature(FrozenStrictModel)` — 필드 `file: str`, `function: str`,
    `digest: str`, `node_count: int`; 프로퍼티 `member -> str` (`"file:function"`)
  - `collect_clone_signatures(relative_path: str, tree: ast.Module) -> list[CloneSignature]`
  - 상수 `CLONE_MIN_AST_NODES: int`, `MECHANICAL_EXCLUDED_NAMES: frozenset[str]`,
    `MECHANICAL_EXCLUDED_ATTRS: frozenset[str]`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_design_ratchet_clones.py` 를 새로 만든다.

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run:
```bash
cd /home/deploy/project/bid-vector-dup-spec && \
/home/deploy/project/bid-vector/.venv/bin/pytest \
  tests/test_design_ratchet_clones.py -v
```
Expected: collection 단계에서 FAIL —
`ModuleNotFoundError: No module named 'scripts._design_ratchet_clones'`

- [ ] **Step 3: 모듈 구현**

`scripts/_design_ratchet_clones.py` 를 새로 만든다.

```python
#!/usr/bin/env python3
"""설계 래칫의 중복 클론 측정 — 메커니컬 헬퍼의 구조 클론 탐지.

스캐너는 이미 measurement(``_design_ratchet_scan.py``) · contracts
(``_design_ratchet_contracts.py``) · CLI(``design_ratchet.py``) 로 나뉘어 있다. 클론
탐지를 네 번째 모듈로 뺀 것도 같은 이유다: 스캐너 자신이 스캔 대상이라 CLAUDE.md
§4.5-4 의 파일 500줄 한도를 지켜야 하는데, 403줄인 scan 모듈에 클론 탐지를 얹으면
한도를 넘겨 ``file_loc_band`` 를 스스로 올린다.

여기의 ``CloneSignature``/``CloneGroup`` 은 baseline JSON 으로 직렬화되지 않는 내부
자료구조라 contracts 모듈이 아니라 이 모듈에 둔다. contracts 의 경계는 "baseline 에
직렬화되는 계약 + 순수 비교"다.

"메커니컬 헬퍼" = 도메인 지식이 없는 함수. 데코레이터가 없고(라우터·celery task 배제),
DB 세션·네트워크·전역 설정에 닿지 않는다. 순수 계산뿐 아니라 얇은 파일 I/O 래퍼도
포함한다 — ``_write_json`` 류가 실제 중복의 최대 밀도 구간이기 때문이다.

정규화는 변수·인자명을 등장 순서 플레이스홀더로 치환해 "이름만 바꾼 복붙"을 같게 보되,
**상수 리터럴은 보존**해 ``round(x, 2)`` 와 ``round(x, 3)`` 을 다르게 본다(보수적 판정).
"""
# ruff: noqa: E402 - imports follow the sys.path bootstrap below.
from __future__ import annotations

import ast
import hashlib
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.schemas._base import FrozenStrictModel  # noqa: E402

# --- 선언적 구성 (§4.5-1·3: 튜닝 표면을 함수 밖 데이터로) --------------------------
# 임계값 20 은 실측 고원의 하단이다: 20 과 25 에서 결과가 같고, 12 로 낮추면 우연의
# 일치가 섞이며 40 으로 올리면 진짜 중복을 놓친다. 20 에서의 실측 인벤토리는 교차파일
# 31그룹/71함수 · 동일파일 5그룹/10함수다(allowlist 적용 전).
CLONE_MIN_AST_NODES = 20

# 본문에 이 이름이 나타나면 도메인·인프라에 결합된 것으로 보고 후보에서 뺀다.
# ``open``/``Path``/``json``/``datetime`` 은 **일부러 넣지 않았다** — 얇은 메커니컬
# I/O 래퍼는 통합 대상이 맞다.
MECHANICAL_EXCLUDED_NAMES = frozenset(
    {
        "self",
        "cls",
        "db",
        "session",
        "Session",
        "engine",
        "select",
        "func",
        "requests",
        "httpx",
        "urllib",
        "socket",
        "subprocess",
        "sys",
        "shutil",
        "logger",
        "logging",
        "settings",
        "input",
        "print",
    }
)
MECHANICAL_EXCLUDED_ATTRS = frozenset(
    {"query", "commit", "add", "execute", "flush", "refresh", "scalar", "scalars"}
)

DIGEST_LENGTH = 12
PLACEHOLDER_PREFIX = "v"
NORMALIZED_FUNCTION_NAME = "f"

_FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


class CloneSignature(FrozenStrictModel):
    """한 함수의 정규화 지문. 같은 ``digest`` 끼리가 구조 클론이다."""

    file: str
    function: str
    digest: str
    node_count: int

    @property
    def member(self) -> str:
        return f"{self.file}:{self.function}"


class _NameNormalizer(ast.NodeTransformer):
    """변수·인자명을 등장 순서 플레이스홀더로 치환한다."""

    def __init__(self) -> None:
        self._seen: dict[str, str] = {}

    def _placeholder(self, name: str) -> str:
        if name not in self._seen:
            self._seen[name] = f"{PLACEHOLDER_PREFIX}{len(self._seen)}"
        return self._seen[name]

    def visit_Name(self, node: ast.Name) -> ast.Name:
        renamed = ast.Name(id=self._placeholder(node.id), ctx=node.ctx)
        return ast.copy_location(renamed, node)

    def visit_arg(self, node: ast.arg) -> ast.arg:
        node.arg = self._placeholder(node.arg)
        node.annotation = None
        return node


def _is_docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def is_mechanical(node: _FunctionNode) -> bool:
    """도메인 지식 없는 헬퍼인가. 데코레이터가 있으면 프레임워크 보일러플레이트다."""
    if node.decorator_list:
        return False
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in MECHANICAL_EXCLUDED_NAMES:
            return False
        if isinstance(child, ast.Attribute) and child.attr in MECHANICAL_EXCLUDED_ATTRS:
            return False
        if isinstance(child, (ast.Global, ast.Nonlocal)):
            return False
    return True


def _normalized_digest(node: _FunctionNode) -> str | None:
    """정규화 본문의 지문. 본문이 docstring 뿐이면 비교 대상이 아니라 ``None``."""
    clone = ast.parse(ast.unparse(node)).body[0]
    if not isinstance(clone, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    clone.name = NORMALIZED_FUNCTION_NAME
    clone.decorator_list = []
    clone.returns = None
    body = clone.body[1:] if clone.body and _is_docstring(clone.body[0]) else clone.body
    if not body:
        return None
    clone.body = body
    normalized = _NameNormalizer().visit(clone)
    ast.fix_missing_locations(normalized)
    encoded = ast.unparse(normalized).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:DIGEST_LENGTH]


def collect_clone_signatures(
    relative_path: str, tree: ast.Module
) -> list[CloneSignature]:
    """한 파일의 메커니컬 헬퍼 지문 목록(파일 I/O 없는 순수 함수)."""
    signatures: list[CloneSignature] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        node_count = sum(1 for _ in ast.walk(node))
        if node_count < CLONE_MIN_AST_NODES or not is_mechanical(node):
            continue
        digest = _normalized_digest(node)
        if digest is None:
            continue
        signatures.append(
            CloneSignature(
                file=relative_path,
                function=node.name,
                digest=digest,
                node_count=node_count,
            )
        )
    return signatures
```

- [ ] **Step 4: 테스트 통과 확인**

Run:
```bash
cd /home/deploy/project/bid-vector-dup-spec && \
/home/deploy/project/bid-vector/.venv/bin/pytest \
  tests/test_design_ratchet_clones.py -v
```
Expected: 9 passed

`test_tiny_function_is_below_threshold` 가 실패하면 임계값 계산이
`sum(1 for _ in ast.walk(node))` 가 아닌 다른 기준으로 들어간 것이다.
`def tiny(value): return value` 의 AST 노드는 6개 안팎이라 20 을 넘을 수 없다.

- [ ] **Step 5: ruff 통과 확인**

Run:
```bash
cd /home/deploy/project/bid-vector-dup-spec && \
/home/deploy/project/bid-vector/.venv/bin/ruff check \
  scripts/_design_ratchet_clones.py
```
Expected: `All checks passed!`

`Iterable`/`Sequence`/`Counter` 가 아직 안 쓰여 F401 이 나오면, 그 import 를 지우고
Task 2 에서 다시 추가한다.

- [ ] **Step 6: 커밋**

```bash
cd /home/deploy/project/bid-vector-dup-spec && \
git add scripts/_design_ratchet_clones.py tests/test_design_ratchet_clones.py && \
git commit -m "feat(quality): 메커니컬 헬퍼 클론 시그니처 추출

데코레이터·도메인 결합 배제 predicate + 이름 정규화 지문. 상수 리터럴은
보존해 round(x,2)/round(x,3) 을 구분한다. 그룹핑은 후속 커밋.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: 클론 그룹핑 + allowlist

지문 목록을 그룹으로 묶고, 교차 파일/동일 파일 카운트와 allowlist 면제를 계산한다.

**Files:**
- Modify: `scripts/_design_ratchet_clones.py` (Task 1 에서 만든 모듈에 추가)
- Test: `tests/test_design_ratchet_clones.py` (클래스 추가)

**Interfaces:**
- Consumes: Task 1 의 `CloneSignature`(`file` · `function` · `digest` · `node_count` ·
  `member`), `collect_clone_signatures`
- Produces:
  - `CloneGroup(FrozenStrictModel)` — 필드 `members: tuple[str, ...]`,
    `files: tuple[str, ...]`, `node_count: int`; 프로퍼티 `key -> str`
  - `cross_file_clone_groups(signatures: Sequence[CloneSignature]) -> list[CloneGroup]`
  - `count_cross_file_clones(signatures: Sequence[CloneSignature]) -> dict[str, int]`
  - `count_local_clones(signatures: Sequence[CloneSignature]) -> int`
  - `unused_allowlist_keys(signatures: Sequence[CloneSignature]) -> list[str]`
  - 상수 `CLONE_ALLOWLIST: dict[str, str]` (키 = 멤버 신원, 값 = 사유)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_design_ratchet_clones.py` 끝에 추가한다. import 문도 함께 넓힌다.

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run:
```bash
cd /home/deploy/project/bid-vector-dup-spec && \
/home/deploy/project/bid-vector/.venv/bin/pytest \
  tests/test_design_ratchet_clones.py -v
```
Expected: FAIL — `ImportError: cannot import name 'cross_file_clone_groups'`

- [ ] **Step 3: 그룹핑 구현**

`scripts/_design_ratchet_clones.py` 의 상수 블록 끝(`_FunctionNode` 정의 위)에
allowlist 를 추가한다.

```python
# 구조는 같지만 통합하면 안 되는 그룹. 키는 **멤버 신원**("파일:함수" 정렬 후 "|" 결합)
# 이지 콘텐츠 해시가 아니다 — 해시로 잡으면 면제된 함수를 사소하게 고칠 때마다 키가
# 바뀌어 빌드가 깨진다. 멤버 신원이면 본문 수정은 통과하고, 복사본이 하나 더 생기면
# 멤버 집합이 달라져 면제가 풀린다. 근거는
# docs/superpowers/specs/2026-08-01-code-duplication-consolidation-design.md §5.3.
CLONE_ALLOWLIST: dict[str, str] = {
    "app/services/award_verification.py:_rate_to_fraction"
    "|app/services/base_amount_basis.py:normalize_winning_rate": (
        "금액 basis 도메인 — 두 함수의 basis 의미가 동일함을 증명하기 전에 합치면"
        " 예정가/기초금액 혼동을 코드에 굳힌다."
    ),
    "app/ai/predictors/registry.py:build_default_predictor_registry"
    "|scripts/backtest_price_predictors.py:build_registry": (
        "CLAUDE.md §4.7-2 팩토리/레지스트리 — 스크립트가 축소 레지스트리를 주입하는"
        " 테스트 격리 seam 이라 통합하면 격리가 사라진다."
    ),
}
```

그리고 파일 끝에 그룹핑 함수를 추가한다.

```python
class CloneGroup(FrozenStrictModel):
    """같은 지문을 공유하는 함수들. ``key`` 는 allowlist 조회에 쓰는 멤버 신원이다."""

    members: tuple[str, ...]
    files: tuple[str, ...]
    node_count: int

    @property
    def key(self) -> str:
        return "|".join(self.members)


def _buckets_by_digest(
    signatures: Iterable[CloneSignature],
) -> dict[str, list[CloneSignature]]:
    buckets: dict[str, list[CloneSignature]] = {}
    for signature in signatures:
        buckets.setdefault(signature.digest, []).append(signature)
    return buckets


def _build_group(members: Sequence[CloneSignature]) -> CloneGroup:
    return CloneGroup(
        members=tuple(sorted(member.member for member in members)),
        files=tuple(sorted({member.file for member in members})),
        node_count=max(member.node_count for member in members),
    )


def _cross_file_buckets(
    signatures: Iterable[CloneSignature],
) -> Iterator[list[CloneSignature]]:
    for members in _buckets_by_digest(signatures).values():
        if len({member.file for member in members}) > 1:
            yield members


def cross_file_clone_groups(
    signatures: Sequence[CloneSignature],
) -> list[CloneGroup]:
    """소속 파일이 2개 이상인 그룹. allowlist 등재분은 제외한다."""
    groups = [
        group
        for members in _cross_file_buckets(signatures)
        if (group := _build_group(members)).key not in CLONE_ALLOWLIST
    ]
    return sorted(groups, key=lambda group: group.key)


def count_cross_file_clones(signatures: Sequence[CloneSignature]) -> dict[str, int]:
    """파일별 교차 파일 클론 멤버 수."""
    counts: Counter[str] = Counter()
    for group in cross_file_clone_groups(signatures):
        counts.update(member.rsplit(":", 1)[0] for member in group.members)
    return dict(counts)


def count_local_clones(signatures: Sequence[CloneSignature]) -> int:
    """한 파일 안에서 서로 클론인 헬퍼 수(같은 파일의 지문만 넘길 것)."""
    return sum(
        len(members)
        for members in _buckets_by_digest(signatures).values()
        if len(members) > 1
    )


def unused_allowlist_keys(signatures: Sequence[CloneSignature]) -> list[str]:
    """현재 스캔에 더 이상 존재하지 않는 allowlist 항목.

    비워 두면 면제 범위가 조용히 넓어지므로 호출부가 실패시킨다.
    """
    live = {_build_group(members).key for members in _cross_file_buckets(signatures)}
    return sorted(set(CLONE_ALLOWLIST) - live)
```

`Iterator` 를 쓰므로 상단 import 를 다음으로 맞춘다:

```python
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
```

- [ ] **Step 4: 테스트 통과 확인**

Run:
```bash
cd /home/deploy/project/bid-vector-dup-spec && \
/home/deploy/project/bid-vector/.venv/bin/pytest \
  tests/test_design_ratchet_clones.py -v
```
Expected: 20 passed

- [ ] **Step 5: 실제 저장소에서 두 allowlist 그룹이 살아 있는지 확인**

allowlist 항목이 처음부터 죽어 있으면 Task 4 의 게이트가 즉시 실패한다. 지금 확인한다.

Run:
```bash
cd /home/deploy/project/bid-vector-dup-spec && \
/home/deploy/project/bid-vector/.venv/bin/python -c "
import ast, pathlib
from scripts._design_ratchet_clones import (
    CLONE_ALLOWLIST, collect_clone_signatures, unused_allowlist_keys,
)
root = pathlib.Path('.')
sigs = []
for d in ('app', 'scripts'):
    for p in sorted(root.joinpath(d).rglob('*.py')):
        if '__pycache__' in p.parts:
            continue
        sigs += collect_clone_signatures(
            p.as_posix(), ast.parse(p.read_text(encoding='utf-8'))
        )
dead = unused_allowlist_keys(sigs)
print('allowlist 항목:', len(CLONE_ALLOWLIST))
print('죽은 항목:', dead or '없음')
"
```
Expected: `죽은 항목: 없음`

죽은 항목이 나오면 그 그룹은 현재 predicate 로 잡히지 않는다는 뜻이다. 해당 함수를
`is_mechanical` 로 개별 검사해 왜 탈락하는지 확인하고, **allowlist 에서 그 항목을
삭제한다**(통합 대상이 아니었다면 애초에 지표에 안 잡히므로 면제가 불필요하다).
predicate 를 느슨하게 고쳐서 억지로 살리지 않는다.

- [ ] **Step 6: ruff 통과 + 커밋**

```bash
cd /home/deploy/project/bid-vector-dup-spec && \
/home/deploy/project/bid-vector/.venv/bin/ruff check \
  scripts/_design_ratchet_clones.py && \
git add scripts/_design_ratchet_clones.py tests/test_design_ratchet_clones.py && \
git commit -m "feat(quality): 클론 그룹핑 + 멤버 신원 키 allowlist

교차파일/동일파일 카운트를 분리하고, 통합하면 안 되는 2그룹(basis 도메인·
predictor 레지스트리 주입 seam)을 사유와 함께 데이터로 선언한다. 죽은
allowlist 항목 탐지 함수 포함.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: 스캐너 배선 (`FileMetrics` 2필드 + `scan_repo` 2-pass)

**Files:**
- Modify: `scripts/_design_ratchet_contracts.py:45-51` (FileMetrics 필드 목록)
- Modify: `scripts/_design_ratchet_scan.py:354-379` (scan_source),
  `scripts/_design_ratchet_scan.py:395-403` (scan_repo)
- Test: `tests/test_design_ratchet.py` (클래스 추가)

**Interfaces:**
- Consumes: Task 2 의 `CloneSignature`, `collect_clone_signatures`,
  `count_cross_file_clones`, `count_local_clones`
- Produces:
  - `FileMetrics.duplicate_mechanical_helpers: int = 0`
  - `FileMetrics.duplicate_mechanical_helpers_local: int = 0`
  - `scan_file(relative_path: str, source: str) -> tuple[FileMetrics, list[CloneSignature]]`
  - `scan_repo_with_signatures(root: Path) -> tuple[RatchetReport, list[CloneSignature]]`
  - `scan_source(relative_path: str, source: str) -> FileMetrics` (기존 계약 유지)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_design_ratchet.py` 의 `TestScanSource` 클래스 **뒤**, `TestRepositoryRatchet`
**앞**에 새 클래스를 넣는다. 파일 상단 import 에 `scan_file` 을 추가한다.

```python
DECLARED_KEY_PROJECTION = """
def project(raw_item):
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


class TestDuplicateHelperMetrics:
    def test_local_clone_pair_is_counted_in_scan_source(self) -> None:
        source = DECLARED_KEY_PROJECTION + DECLARED_KEY_PROJECTION.replace(
            "def project", "def project_other"
        )
        metrics = scan_source("app/sample.py", source)
        assert metrics.duplicate_mechanical_helpers_local == 2

    def test_single_helper_has_no_local_clone(self) -> None:
        metrics = scan_source("app/sample.py", DECLARED_KEY_PROJECTION)
        assert metrics.duplicate_mechanical_helpers_local == 0

    def test_scan_source_never_sets_cross_file_metric(self) -> None:
        """교차 파일 지표는 한 파일만 봐서는 알 수 없다 — pass 2 의 책임이다."""
        metrics = scan_source("app/sample.py", DECLARED_KEY_PROJECTION)
        assert metrics.duplicate_mechanical_helpers == 0

    def test_scan_file_returns_signatures(self) -> None:
        metrics, signatures = scan_file("app/sample.py", DECLARED_KEY_PROJECTION)
        assert metrics.duplicate_mechanical_helpers_local == 0
        assert [signature.function for signature in signatures] == ["project"]

    def test_scan_repo_attributes_cross_file_clones(self, tmp_path: Path) -> None:
        for directory in ("app", "scripts"):
            (tmp_path / directory).mkdir()
        (tmp_path / "app" / "one.py").write_text(
            DECLARED_KEY_PROJECTION, encoding="utf-8"
        )
        (tmp_path / "scripts" / "two.py").write_text(
            DECLARED_KEY_PROJECTION.replace("def project", "def project_copy"),
            encoding="utf-8",
        )
        report = scan_repo(tmp_path)
        assert report.metrics_for("app/one.py").duplicate_mechanical_helpers == 1
        assert report.metrics_for("scripts/two.py").duplicate_mechanical_helpers == 1

    def test_scan_repo_leaves_unique_helpers_clean(self, tmp_path: Path) -> None:
        for directory in ("app", "scripts"):
            (tmp_path / directory).mkdir()
        (tmp_path / "app" / "one.py").write_text(
            DECLARED_KEY_PROJECTION, encoding="utf-8"
        )
        (tmp_path / "scripts" / "two.py").write_text(
            "def tiny(value):\n    return value\n", encoding="utf-8"
        )
        report = scan_repo(tmp_path)
        assert report.files == {}

    def test_new_metrics_are_part_of_the_ratchet_contract(self) -> None:
        assert "duplicate_mechanical_helpers" in METRIC_NAMES
        assert "duplicate_mechanical_helpers_local" in METRIC_NAMES
```

`Path` 는 이미 import 되어 있다. `METRIC_NAMES` 는 **contracts 모듈**에, `scan_file` 은
scan 모듈에 있으므로 기존 import 블록 두 개를 각각 넓힌다:

```python
from scripts._design_ratchet_contracts import (
    METRIC_NAMES,
    FileMetrics,
    RatchetReport,
    compare_reports,
    count_improvements,
    without_paths,
)
from scripts._design_ratchet_scan import (
    REPO_ROOT,
    RatchetScanError,
    file_loc_band,
    scan_file,
    scan_repo,
    scan_source,
)
```

- [ ] **Step 2: 테스트 실패 확인**

Run:
```bash
cd /home/deploy/project/bid-vector-dup-spec && \
/home/deploy/project/bid-vector/.venv/bin/pytest \
  tests/test_design_ratchet.py::TestDuplicateHelperMetrics -v
```
Expected: FAIL — `ImportError: cannot import name 'scan_file'`

- [ ] **Step 3: `FileMetrics` 확장**

신규 필드는 baseline JSON 으로 직렬화되므로 **contracts 모듈**이 자리다.
`scripts/_design_ratchet_contracts.py:45-51` 의 `FileMetrics` 필드 목록 끝에 두 줄을
추가한다(교체 후 전체 필드 목록).

```python
    functions_over_soft_limit: int = 0
    functions_over_hard_limit: int = 0
    file_loc_band: int = 0
    json_direct_calls: int = 0
    dict_boundary_functions: int = 0
    env_test_sniff: int = 0
    unvalidated_dict_tasks: int = 0
    duplicate_mechanical_helpers: int = 0
    duplicate_mechanical_helpers_local: int = 0
```

기본값이 0 이고 `save_baseline` 이 `exclude_defaults=True` 로 저장하므로 **기존 baseline
JSON 과 스키마 호환**이다(`_is_corrupt_baseline` 경로를 타지 않는다). `METRIC_NAMES` 는
`tuple(FileMetrics.model_fields)` 라서 두 지표가 자동으로 래칫 계약에 편입되고,
`is_clean()`·`totals()`·`compare_reports` 도 별도 수정 없이 새 지표를 다룬다.

- [ ] **Step 4: import 추가**

`scripts/_design_ratchet_scan.py` 상단에는 이미 contracts import 블록이 있다.

```python
from scripts._design_ratchet_contracts import (  # noqa: E402
    FILE_LOC_BAND_LINES,
    FILE_LOC_SOFT_LIMIT,
    FileMetrics,
    RatchetReport,
)
```

이 블록 **바로 아래**에 clones import 를 넣는다(ruff 규칙 집합에 isort(`I`) 가 없어
정렬은 강제되지 않는다 — 인접 배치로 읽기 좋게만 둔다).

```python
from scripts._design_ratchet_clones import (  # noqa: E402
    CloneSignature,
    collect_clone_signatures,
    count_cross_file_clones,
    count_local_clones,
)
```

- [ ] **Step 5: `scan_source` 를 `scan_file` 로 분리**

`scripts/_design_ratchet_scan.py:354-379` 의 `scan_source` 를 통째로 교체한다. 현재
원문(앵커)은 다음과 같다.

```python
def scan_source(relative_path: str, source: str) -> FileMetrics:
    """한 파일의 소스 텍스트에서 지표를 센다(파일 I/O 없는 순수 함수)."""
    tree = _parse_source(relative_path, source)
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    return FileMetrics(
        functions_over_soft_limit=_count_functions_over(
            functions, FUNCTION_SOFT_LIMIT_LINES
        ),
        functions_over_hard_limit=_count_functions_over(
            functions, FUNCTION_HARD_LIMIT_LINES
        ),
        file_loc_band=file_loc_band(source),
        json_direct_calls=_count_json_direct_calls(tree, relative_path),
        dict_boundary_functions=sum(
            1 for node in functions if _has_weak_boundary(node)
        ),
        env_test_sniff=_count_env_test_sniff(tree, relative_path),
        unvalidated_dict_tasks=sum(
            1 for node in functions if _is_unvalidated_dict_task(node)
        ),
    )
```

교체본이다. 기존 7개 지표의 계산식은 **한 글자도 바꾸지 않고** 그대로 옮기고
`duplicate_mechanical_helpers_local` 한 줄만 더한다(PR1 은 기존 지표를 움직이지
않는다 — Task 5 Step 1 이 이를 검증한다). `Sequence` 는 scan 모듈 상단이 이미
import 하고 있으므로 추가 import 는 필요 없다.

```python
def _metrics_from_tree(
    relative_path: str,
    source: str,
    tree: ast.Module,
    signatures: Sequence[CloneSignature],
) -> FileMetrics:
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    return FileMetrics(
        functions_over_soft_limit=_count_functions_over(
            functions, FUNCTION_SOFT_LIMIT_LINES
        ),
        functions_over_hard_limit=_count_functions_over(
            functions, FUNCTION_HARD_LIMIT_LINES
        ),
        file_loc_band=file_loc_band(source),
        json_direct_calls=_count_json_direct_calls(tree, relative_path),
        dict_boundary_functions=sum(
            1 for node in functions if _has_weak_boundary(node)
        ),
        env_test_sniff=_count_env_test_sniff(tree, relative_path),
        unvalidated_dict_tasks=sum(
            1 for node in functions if _is_unvalidated_dict_task(node)
        ),
        duplicate_mechanical_helpers_local=count_local_clones(signatures),
    )


def scan_file(
    relative_path: str, source: str
) -> tuple[FileMetrics, list[CloneSignature]]:
    """지표 + 클론 지문을 한 번의 파싱으로 얻는다(파일 I/O 없는 순수 함수).

    ``duplicate_mechanical_helpers`` 는 교차 파일 정보가 필요하므로 여기서는 0 이고
    ``scan_repo`` 의 pass 2 에서 채워진다.
    """
    tree = _parse_source(relative_path, source)
    signatures = collect_clone_signatures(relative_path, tree)
    return _metrics_from_tree(relative_path, source, tree, signatures), signatures


def scan_source(relative_path: str, source: str) -> FileMetrics:
    """한 파일의 소스 텍스트에서 지표를 센다(파일 I/O 없는 순수 함수)."""
    return scan_file(relative_path, source)[0]
```

- [ ] **Step 6: `scan_repo` 를 2-pass 로**

`scripts/_design_ratchet_scan.py:395-403` 의 `scan_repo` 를 교체하고, 그 아래에
`scan_repo_with_signatures` 를 그 자리에 둔다. 현재 원문(앵커)은 다음과 같다.

```python
def scan_repo(root: Path) -> RatchetReport:
    """대상 디렉터리를 스캔한다. 위반 0 인 파일은 리포트에 넣지 않는다."""
    report = RatchetReport()
    for path in iter_target_files(root):
        relative_path = path.relative_to(root).as_posix()
        metrics = scan_source(relative_path, _read_source(path, relative_path))
        if not metrics.is_clean():
            report.files[relative_path] = metrics
    return report
```

교체본이다.

```python
def scan_repo_with_signatures(
    root: Path,
) -> tuple[RatchetReport, list[CloneSignature]]:
    """대상 디렉터리를 스캔해 리포트와 저장소 전체 클론 지문을 함께 돌려준다.

    2-pass 다. pass 1 은 파일별 순수 스캔, pass 2 는 교차 파일 클론 귀속이다. 파일
    단위로는 다른 파일의 존재를 알 수 없어서 한 번에 끝낼 수 없다.

    pass 1 은 **위반 0 인 파일도 반드시 수집한다.** 미리 걸러내면 그 파일의 클론이
    교차 파일 그룹 계산에서 빠져 다른 파일의 카운트까지 틀어진다. 같은 이유로
    ``is_clean()`` 필터는 pass 2 의 병합 **뒤에** 적용한다 — 먼저 적용하면 클론만
    가진 파일이 리포트에서 사라진다.
    """
    scanned: list[tuple[str, FileMetrics]] = []
    signatures: list[CloneSignature] = []
    for path in iter_target_files(root):
        relative_path = path.relative_to(root).as_posix()
        metrics, file_signatures = scan_file(
            relative_path, _read_source(path, relative_path)
        )
        scanned.append((relative_path, metrics))
        signatures.extend(file_signatures)

    cross_file_counts = count_cross_file_clones(signatures)

    report = RatchetReport()
    for relative_path, metrics in scanned:
        duplicates = cross_file_counts.get(relative_path, 0)
        if duplicates:
            metrics = metrics.model_copy(
                update={"duplicate_mechanical_helpers": duplicates}
            )
        if not metrics.is_clean():
            report.files[relative_path] = metrics
    return report, signatures


def scan_repo(root: Path) -> RatchetReport:
    """대상 디렉터리를 스캔한다. 위반 0 인 파일은 리포트에 넣지 않는다."""
    return scan_repo_with_signatures(root)[0]
```

`scan_repo` 는 얇은 델리게이터로 남는다 — 시그니처·반환 타입·docstring 이 그대로라
기존 테스트와 `scripts/design_ratchet.py` 호출부가 영향받지 않는다. 시그니처가 필요한
호출부(Task 4 의 CLI)는 `scan_repo_with_signatures` 를 **한 번** 호출해 둘 다 얻는다.
저장소를 두 번 파싱하는 경로를 만들지 마라.

- [ ] **Step 7: 테스트 통과 확인**

Run:
```bash
cd /home/deploy/project/bid-vector-dup-spec && \
/home/deploy/project/bid-vector/.venv/bin/pytest \
  tests/test_design_ratchet.py::TestDuplicateHelperMetrics -v
```
Expected: 7 passed

`test_scan_repo_leaves_unique_helpers_clean` 이 실패하면 `DECLARED_KEY_PROJECTION` 이
`app/one.py` 에서 `duplicate_mechanical_helpers_local` 을 올린 것이다. 그 소스에
동일 지문 함수가 하나뿐인지 확인한다.

- [ ] **Step 8: 기존 테스트 회귀 확인**

Run:
```bash
cd /home/deploy/project/bid-vector-dup-spec && \
/home/deploy/project/bid-vector/.venv/bin/pytest tests/test_design_ratchet.py -v
```
Expected: `TestRepositoryRatchet::test_current_repository_does_not_exceed_baseline`
**만** 실패한다(신규 지표가 baseline 에 아직 없으므로 정상). 나머지는 전부 통과.

이 실패는 Task 5 에서 baseline 을 재생성하며 해소한다. 다른 테스트가 실패하면 그건
회귀이므로 먼저 고친다.

- [ ] **Step 9: 커밋**

```bash
cd /home/deploy/project/bid-vector-dup-spec && \
/home/deploy/project/bid-vector/.venv/bin/ruff check \
  scripts/_design_ratchet_scan.py scripts/_design_ratchet_clones.py \
  scripts/_design_ratchet_contracts.py && \
git add scripts/_design_ratchet_contracts.py scripts/_design_ratchet_scan.py \
  tests/test_design_ratchet.py && \
git commit -m "feat(quality): 중복 헬퍼 지표 2개를 래칫에 배선

contracts 의 FileMetrics 에 duplicate_mechanical_helpers(교차파일) 와
_local(동일파일) 추가. scan_source 의 파일 단위 순수 함수 계약은 유지하고,
교차 파일 정보가 필요한 지표만 scan_repo 의 pass 2 에서 귀속한다.

baseline 은 아직 미갱신이라 저장소 게이트는 의도적으로 실패 상태다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: CLI 리포트 + 죽은 allowlist 게이트 + CI ruff 대상

**Files:**
- Modify: `scripts/design_ratchet.py` (import 블록 확장 · 포매터 2개 추가 · `main()`)
- Modify: `.github/workflows/ci.yml` (Ruff 스텝의 `run:` folded 스칼라)
- Test: `tests/test_design_ratchet.py` (클래스 추가)

**Interfaces:**
- Consumes: Task 2 의 `unused_allowlist_keys`, `cross_file_clone_groups`,
  `CLONE_ALLOWLIST`; Task 3 의 `scan_repo_with_signatures`

> **설계 변경 (Task 3 리뷰 결과, 사람 승인).** 원안의
> `scan_repo_clone_signatures(root) -> list[CloneSignature]` 는 **삭제됐다.**
> 그 함수는 `scan_repo` 가 pass 1 에서 이미 모아 버리는 시그니처를 재구축하려고
> 저장소 전체를 다시 읽고 다시 파싱했다 — CLI 가 둘 다 부르면 378개 파일을 두 번
> 파싱하는 중복 스캔 루프였다. 중복 제거가 주제인 PR 에서 용납할 수 없고, 본문이 달라
> 우리 탐지기 자신도 못 잡는다.
>
> 대신 Task 3 이 한 단계 아래에서 쓴 패턴을 그대로 올렸다:
>
> ```
> scan_source(path, source) -> FileMetrics                               # 얇은 델리게이터
> scan_file(path, source)   -> tuple[FileMetrics, list[CloneSignature]]  # 실제 작업
>
> scan_repo(root)                 -> RatchetReport                              # 얇은 델리게이터
> scan_repo_with_signatures(root) -> tuple[RatchetReport, list[CloneSignature]] # 실제 작업
> ```
>
> `scan_repo` 의 공개 계약은 불변이므로 기존 테스트와 호출부는 영향이 없다. CLI 는
> `scan_repo_with_signatures` 를 **한 번** 호출해 리포트와 시그니처를 동시에 얻는다.
- Produces:
  - `format_clone_groups(groups: Sequence[CloneGroup]) -> str`
  - `format_dead_allowlist(keys: Sequence[str]) -> str`
  - `main()` 이 죽은 allowlist 항목이 있으면 exit 1

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_design_ratchet.py` 에 추가한다.

```python
class TestCloneReporting:
    def test_format_clone_groups_lists_members(self) -> None:
        group = CloneGroup(
            members=("app/a.py:write", "app/b.py:dump"),
            files=("app/a.py", "app/b.py"),
            node_count=53,
        )
        rendered = format_clone_groups([group])
        assert "app/a.py:write" in rendered
        assert "app/b.py:dump" in rendered
        assert "53" in rendered

    def test_format_clone_groups_handles_empty(self) -> None:
        assert "없음" in format_clone_groups([])

    def test_format_dead_allowlist_lists_keys(self) -> None:
        rendered = format_dead_allowlist(["app/a.py:x|app/b.py:y"])
        assert "app/a.py:x|app/b.py:y" in rendered

    def test_repository_has_no_dead_allowlist_entries(self) -> None:
        """면제가 stale 해지면 범위가 조용히 넓어진다 — 죽은 항목은 즉시 실패다."""
        _, signatures = scan_repo_with_signatures(REPO_ROOT)
        dead = unused_allowlist_keys(signatures)
        assert not dead, format_dead_allowlist(dead)
```

파일 상단 import 에 추가한다(`scan_repo_with_signatures` 는 Task 3 이 이미 넣었을 수
있으니 중복 import 하지 말고 기존 블록을 넓혀라):

```python
from scripts._design_ratchet_clones import (
    CloneGroup,
    unused_allowlist_keys,
)
from scripts._design_ratchet_scan import scan_repo_with_signatures
from scripts.design_ratchet import format_clone_groups, format_dead_allowlist
```

- [ ] **Step 2: 테스트 실패 확인**

Run:
```bash
cd /home/deploy/project/bid-vector-dup-spec && \
/home/deploy/project/bid-vector/.venv/bin/pytest \
  tests/test_design_ratchet.py::TestCloneReporting -v
```
Expected: FAIL — `ImportError: cannot import name 'format_clone_groups'`

- [ ] **Step 3: CLI 구현**

`scripts/design_ratchet.py` 는 이제 contracts 와 scan **양쪽**에서 심볼을 가져온다.
**먼저 파일을 읽어 현재 import 블록을 확인한 뒤** 아래 두 가지를 반영한다(라인 번호에
의존하지 말 것 — 이 파일은 275줄이고 블록 위치가 바뀔 수 있다).

1. 기존 contracts import 블록은 **그대로 둔다**(`FILE_LOC_BAND_LINES` ·
   `FILE_LOC_METRIC` · `FILE_LOC_SOFT_LIMIT` · `METRIC_NAMES` · `FileMetrics` ·
   `RatchetReport` · `RatchetViolation` · `compare_reports` · `count_improvements` ·
   `without_paths`).
2. 그 뒤에 clones import 블록을 새로 넣고, 기존
   `from scripts._design_ratchet_scan import scan_repo` 한 줄을
   `scan_repo_with_signatures` 로 **교체**한다. CLI 는 리포트와 시그니처가 둘 다
   필요하므로 `scan_repo` 는 더 이상 쓰지 않는다(`scan_repo` 자체는 다른 호출부·
   테스트를 위해 모듈에 그대로 남아 있다).

```python
from scripts._design_ratchet_clones import (  # noqa: E402
    CLONE_ALLOWLIST,
    CloneGroup,
    cross_file_clone_groups,
    unused_allowlist_keys,
)
from scripts._design_ratchet_scan import scan_repo_with_signatures  # noqa: E402
```

`METRIC_TOTAL_FORMATTERS` 정의 아래에 포매터 두 개를 추가한다.

```python
CLONE_GROUP_PREVIEW_LIMIT = 30

DEAD_ALLOWLIST_HINT = (
    "CLONE_ALLOWLIST 에 등재됐지만 현재 스캔에 없는 항목입니다."
    " 면제가 stale 해지면 범위가 조용히 넓어지므로 해당 항목을 삭제하세요."
)


def format_clone_groups(groups: Sequence[CloneGroup]) -> str:
    """통합 대상 교차 파일 클론 그룹(allowlist 제외분)."""
    if not groups:
        return "교차 파일 클론 그룹: 없음"
    header = f"교차 파일 클론 그룹 {len(groups)}개 (allowlist {len(CLONE_ALLOWLIST)}개 제외):"
    lines = [header]
    for group in sorted(groups, key=lambda item: -item.node_count)[
        :CLONE_GROUP_PREVIEW_LIMIT
    ]:
        lines.append(f"  ~{group.node_count}노드 · {len(group.members)}벌")
        lines.extend(f"      {member}" for member in group.members)
    hidden = len(groups) - min(len(groups), CLONE_GROUP_PREVIEW_LIMIT)
    if hidden:
        lines.append(f"  ... 외 {hidden}개 그룹")
    return "\n".join(lines)


def format_dead_allowlist(keys: Sequence[str]) -> str:
    if not keys:
        return "죽은 allowlist 항목: 없음"
    lines = [f"죽은 allowlist 항목 {len(keys)}개:", *(f"  {key}" for key in keys)]
    lines.append(DEAD_ALLOWLIST_HINT)
    return "\n".join(lines)
```

`main()` 을 다음으로 교체한다. **교체 전에 현재 `main()` 본문을 읽어 아래와 대조한다** —
아래는 `format_totals` → baseline 비교 → 위반 출력 흐름에 클론 리포트와 죽은 allowlist
게이트만 끼워 넣은 것이므로, 현재 본문에 이 플랜이 모르는 분기가 더 있으면 그 분기를
살린 채 삽입한다.

```python
def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    report, signatures = scan_repo_with_signatures(REPO_ROOT)
    print(format_totals(report))

    print(format_clone_groups(cross_file_clone_groups(signatures)))
    dead = unused_allowlist_keys(signatures)
    if dead:
        print(format_dead_allowlist(dead))

    if args.update_baseline:
        return run_baseline_update(report)
    if not BASELINE_PATH.exists():
        print(MISSING_BASELINE_MESSAGE)
        return 1
    violations = compare_reports(load_baseline(BASELINE_PATH), report)
    if not violations and not dead:
        print("위반 없음 (baseline 이하)")
        return 0
    if violations:
        print(format_violations(violations))
        print(UPDATE_BASELINE_HINT)
    return 1
```

죽은 allowlist 는 `--update-baseline` 경로에서는 **차단하지 않는다**(정리 중에 항목이
사라지는 것은 정상 경과이고, 그 때 리포트만 보여 주면 된다).

- [ ] **Step 4: CI ruff 대상에 신규 모듈 추가**

Ruff 스텝의 `run:` 은 folded 스칼라(`>-`)다. 현재 원문(앵커)은 다음과 같다.

```yaml
      - name: Ruff (lint)
        # 설계 래칫 스캐너는 자신이 검사하는 규율을 지켜야 하므로 lint 대상에 포함한다
        # (scripts/ 전체는 아직 grandfathered — docs/operations/design-ratchet.md).
        run: >-
          ruff check app/ scripts/design_ratchet.py
          scripts/_design_ratchet_scan.py scripts/_design_ratchet_contracts.py
```

마지막 줄 끝에 신규 모듈을 덧붙인다(88자를 넘지 않게 줄을 나눈다).

```yaml
      - name: Ruff (lint)
        # 설계 래칫 스캐너는 자신이 검사하는 규율을 지켜야 하므로 lint 대상에 포함한다
        # (scripts/ 전체는 아직 grandfathered — docs/operations/design-ratchet.md).
        run: >-
          ruff check app/ scripts/design_ratchet.py
          scripts/_design_ratchet_scan.py scripts/_design_ratchet_contracts.py
          scripts/_design_ratchet_clones.py
```

folded 스칼라는 이어진 줄을 공백 하나로 접합하므로 결과 명령은 한 줄짜리
`ruff check app/ scripts/design_ratchet.py scripts/_design_ratchet_scan.py
scripts/_design_ratchet_contracts.py scripts/_design_ratchet_clones.py` 와 같다.

- [ ] **Step 5: 테스트 통과 확인**

Run:
```bash
cd /home/deploy/project/bid-vector-dup-spec && \
/home/deploy/project/bid-vector/.venv/bin/pytest \
  tests/test_design_ratchet.py::TestCloneReporting -v
```
Expected: 4 passed

- [ ] **Step 6: CLI 실제 실행 — 통합 대상 목록 확보**

이 출력이 PR2·PR3 의 작업 목록이다. 결과를 PR 본문에 붙일 수 있게 저장한다.

Run:
```bash
cd /home/deploy/project/bid-vector-dup-spec && \
/home/deploy/project/bid-vector/.venv/bin/python scripts/design_ratchet.py \
  2>&1 | tee /tmp/claude-1000/-home-deploy-project-bid-vector/45e371cb-61c9-4a2c-b926-c0edd6fa8158/scratchpad/ratchet-pr1.txt
```
Expected: `duplicate_mechanical_helpers` · `duplicate_mechanical_helpers_local` 총계가
0 보다 크고, "교차 파일 클론 그룹 N개" 목록이 출력되며, `죽은 allowlist 항목` 줄은
나타나지 않는다. 종료 코드는 1 (baseline 미갱신 상태이므로 정상).

출력에 스펙 §5.2 의 대표 그룹이 보이는지 눈으로 확인한다: `_write_json` ·
`_read_json_object` · `parse_thresholds` · `_count_lines` ·
`extract_eligibility_flags`. 이 중 하나라도 없으면 Task 1~2 의 predicate 를 재점검한다.

- [ ] **Step 7: 커밋**

```bash
cd /home/deploy/project/bid-vector-dup-spec && \
/home/deploy/project/bid-vector/.venv/bin/ruff check \
  scripts/design_ratchet.py scripts/_design_ratchet_scan.py \
  scripts/_design_ratchet_contracts.py scripts/_design_ratchet_clones.py && \
git add scripts/design_ratchet.py tests/test_design_ratchet.py .github/workflows/ci.yml && \
git commit -m "feat(quality): 클론 그룹 리포트 + 죽은 allowlist 게이트

CLI 가 통합 대상 그룹을 출력하고, CLONE_ALLOWLIST 에 등재됐으나 현재
스캔에 없는 항목이 있으면 실패시킨다(면제가 stale 해지며 조용히 넓어지는
것을 차단). ruff 대상에 신규 스캐너 모듈 추가.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: baseline 동결

여기서부터 새 중복이 CI 에서 차단된다.

**Files:**
- Modify: `tests/design_ratchet_baseline.json`

**Interfaces:**
- Consumes: Task 3 의 `scan_repo`(신규 2지표 포함), Task 4 의 CLI
- Produces: 신규 2지표 값이 기록된 baseline. 이후 모든 태스크·PR 의 기준선

- [ ] **Step 1: 갱신 전 delta 확인**

Run:
```bash
cd /home/deploy/project/bid-vector-dup-spec && \
/home/deploy/project/bid-vector/.venv/bin/python scripts/design_ratchet.py \
  --update-baseline
```
Expected: `baseline delta` 블록에 **증가(위반)** 가 신규 2지표에서만 발생하고,
`감소 총량: 없음` 이며, `신규 등장 파일` 은 기존 baseline 에 없던(위반 0 이었지만
이제 중복 지표가 붙은) 파일들이다. 마지막에 `baseline 재생성: .../design_ratchet_baseline.json`.

기존 7개 지표에서 증가가 하나라도 있으면 **중단한다.** PR1 은 애플리케이션 코드를
건드리지 않으므로 기존 지표는 변할 수 없다. 변했다면 Task 3 의 `_metrics_from_tree`
리팩터에서 계산이 바뀐 것이므로 원인을 찾아 고치고 baseline 을 되돌린다:
`git checkout tests/design_ratchet_baseline.json`

- [ ] **Step 2: 기록된 수치 확인**

Run:
```bash
cd /home/deploy/project/bid-vector-dup-spec && \
/home/deploy/project/bid-vector/.venv/bin/python -c "
import json
data = json.load(open('tests/design_ratchet_baseline.json'))
cross = sum(
    m.get('duplicate_mechanical_helpers', 0) for m in data['files'].values()
)
local = sum(
    m.get('duplicate_mechanical_helpers_local', 0) for m in data['files'].values()
)
print('교차파일 중복 함수:', cross)
print('동일파일 중복 함수:', local)
print('baseline 파일 수:', len(data['files']))
"
```
Expected: `교차파일 중복 함수: 67` · `동일파일 중복 함수: 10`

근거는 재측정된 인벤토리다: 교차파일 **31그룹 / 71함수**, 동일파일 **5그룹 / 10함수**.
여기서 `CLONE_ALLOWLIST` 의 2그룹(4함수)이 지표에서 제외되므로 baseline 이 기록하는
교차파일 값은 71 − 4 = **67**, 동일파일은 면제 대상이 없어 **10** 이다.

이 수치는 **통과 조건이 아니라 기대치**다. 구현된 predicate 가 측정 스크립트와 완전히
같지 않을 수 있어 소폭 차이는 정상이다. 다만 크게 어긋나면(교차가 0 이거나 200 이상)
Task 1~2 의 predicate 를 재점검한다. 실제 기록값을 PR 본문에 적는다.

- [ ] **Step 3: 게이트가 이제 통과하는지 확인**

Run:
```bash
cd /home/deploy/project/bid-vector-dup-spec && \
/home/deploy/project/bid-vector/.venv/bin/pytest tests/test_design_ratchet.py -v
```
Expected: 전부 통과 (Task 3 Step 8 에서 실패하던
`test_current_repository_does_not_exceed_baseline` 포함)

- [ ] **Step 4: 래칫이 실제로 새 중복을 막는지 실증**

동결이 작동하는지 확인한다. 임시 파일을 만들어 게이트가 실패하는지 본다.

Run:
```bash
cd /home/deploy/project/bid-vector-dup-spec && \
cp app/utils/sequence_coercion.py app/utils/_ratchet_probe.py && \
/home/deploy/project/bid-vector/.venv/bin/python scripts/design_ratchet.py; \
echo "종료코드: $?"; \
rm app/utils/_ratchet_probe.py
```
Expected: 위반이 보고되고 종료코드 1. 위반 목록에
`duplicate_mechanical_helpers: app/utils/_ratchet_probe.py 0 -> N` 이 나타난다.

`rm` 이 실행됐는지 반드시 확인한다:
```bash
cd /home/deploy/project/bid-vector-dup-spec && \
test ! -f app/utils/_ratchet_probe.py && echo "probe 제거 확인"
```

- [ ] **Step 5: 커밋**

```bash
cd /home/deploy/project/bid-vector-dup-spec && \
git status --short && \
git add tests/design_ratchet_baseline.json && \
git commit -m "chore(quality): 중복 헬퍼 지표 baseline 동결

duplicate_mechanical_helpers / _local 의 현재값을 baseline 에 기록한다.
이 시점부터 새 중복은 CI 에서 차단된다. 기존 7개 지표는 불변(PR1 은
애플리케이션 코드를 건드리지 않는다).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

`git status --short` 에 `tests/design_ratchet_baseline.json` 외의 파일이 보이면
probe 파일이 남은 것이다. 커밋 전에 제거한다.

---

### Task 6: 룰 문서화

**Files:**
- Modify: `CLAUDE.md` (§4.5-6 · §4.5-8 신설 · §9)
- Modify: `docs/operations/design-ratchet.md`

**Interfaces:**
- Consumes: Task 1~5 가 만든 지표명·상수명·CLI 명령
- Produces: 없음 (문서)

- [ ] **Step 1: `CLAUDE.md` §4.5-6 에 허브 주소표 추가**

§4.5 의 `### 6. 패턴 활용 (재사용 우선, 복붙 금지)` 마지막 줄
(`- 같은 문제를 두 번째로 풀면 **공용 헬퍼/모듈로 추출**합니다. 복붙·중복 로직 금지.`)
**바로 뒤**에 삽입한다.

```markdown
- 헬퍼를 새로 쓰기 전에 **아래 주소를 먼저 grep** 합니다. 있으면 재사용하고, 없으면
  거기에 만듭니다.

| 용도 | 허브 |
|---|---|
| 숫자 변환·집계 | `app/utils/numeric.py` |
| JSON 읽기/쓰기 | `app/utils/jsonio.py` |
| 문자열·포맷 | `app/utils/textfmt.py` |
| 시퀀스 강제변환 | `app/utils/sequence_coercion.py` |
| 시간 | `app/core/time.py` |
| 교차 모듈 도메인 상수 | `app/core/constants.py` |
| 런타임·환경 설정 | `app/core/config.py` |
| CLI 인자 (stdlib-only) | `scripts/_common/cliargs.py` |
| 프론트 포맷 | `@/shared/format` |
```

`app/utils/numeric.py` · `jsonio.py` · `textfmt.py` · `scripts/_common/cliargs.py` ·
`@/shared/format` 은 PR2·PR3·PR5 에서 생긴다. PR1 시점에 아직 없는 주소지만, 주소표가
먼저 있어야 그 PR 들이 어디로 모을지 흔들리지 않는다.

- [ ] **Step 2: `CLAUDE.md` §4.5-8 신설**

§4.5 의 `### 7. 이벤트 드리븐 + 스트림 데이터 파이프라인 유지` 블록이 끝나는 지점,
`## 4.6 구현 규율` **바로 앞**에 삽입한다.

```markdown
### 8. 중복 금지 (허브에만 정의 · 래칫이 차단)

- 메커니컬 헬퍼(숫자 변환·JSON I/O·문자열 포맷·CLI 인자 파싱)는 **허브에만** 정의합니다.
  위 6의 주소표를 먼저 grep 하고, 없으면 허브에 만들어 거기서 import 합니다.
- 교차 파일 중복은 `duplicate_mechanical_helpers` 가, 동일 파일 중복은
  `duplicate_mechanical_helpers_local` 이 **자동 차단**합니다. 두 지표 중 하나라도
  baseline 보다 오르는 PR 은 CI 에서 실패합니다.
- 같은 알고리즘이 **상수나 캐스트만 달라** 반복되면 복사하지 말고
  **파라미터화된 해석기 + 얇은 명명 래퍼**로 씁니다. 이름의 가독성은 래퍼가 지키고,
  알고리즘은 한 벌만 존재합니다. 래퍼는 임계값(20 AST 노드) 아래라 계수되지 않습니다.

```python
# Bad — 알고리즘이 두 벌
def _int_or_none(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _float_or_none(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# Good — 해석기 한 벌 + 얇은 명명 래퍼
def _cast_or_none(value, cast):
    try:
        return cast(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _int_or_none(value):
    return _cast_or_none(value, int)


def _float_or_none(value):
    return _cast_or_none(value, float)
```

- 통합하면 안 되는 예외는 `scripts/_design_ratchet_clones.py` 의 `CLONE_ALLOWLIST` 에
  **사유와 함께** 등재합니다(주석으로 넘기지 않습니다). 등재됐지만 더 이상 존재하지
  않는 항목은 게이트가 실패시키므로, 면제 범위가 조용히 넓어지지 않습니다.
```

- [ ] **Step 3: `CLAUDE.md` §9 체크리스트에 항목 추가**

§9 의 `- [ ] 테스트 용이성(§4.7): ...` 줄 **바로 뒤**에 추가한다.

```markdown
- [ ] 설계 래칫 통과: `python scripts/design_ratchet.py` 종료코드 0 (증가 시 위반을 없애는 것이 기본 대응, `--update-baseline` 은 사유를 PR 본문에 기재)
```

- [ ] **Step 4: `docs/operations/design-ratchet.md` 지표표에 2행 추가**

> 이 문서는 main 에서 크게 늘었다(탐지기 강화·allowlist 절·변경 이력 추가). **Step 4·5 를
> 시작하기 전에 문서 전체를 먼저 읽고** `## 지표` 표의 마지막 행과 판정 절들의 실제
> 위치를 확인한다. 아래 앵커 문구가 그대로 없으면 같은 역할을 하는 위치에 넣는다 —
> 삽입할 마크다운 본문 자체는 바꾸지 않는다.

`## 지표` 표의 마지막 행(`unvalidated_dict_tasks`) **뒤**에 추가한다.

```markdown
| `duplicate_mechanical_helpers` | 그 파일이 정의한 메커니컬 헬퍼 중 **다른 파일**의 헬퍼와 구조 클론 그룹을 이루는 함수 수 | §4.5-8 중복 금지. 처방은 허브로 이동 |
| `duplicate_mechanical_helpers_local` | 그 파일 **안에서만** 서로 구조 클론인 메커니컬 헬퍼 수 | §4.5-8. 처방은 파라미터화된 해석기 + 얇은 명명 래퍼 |
```

- [ ] **Step 5: `docs/operations/design-ratchet.md` 에 판정 절 추가**

지표별 판정 절들이 끝나는 지점, 즉 마지막 판정 절(현재는
`### \`env_test_sniff\` 의 탐지 범위(한정)`) **뒤**이자 `스캔 대상은 ...` 문단 **앞**에
삽입한다. 판정 절 구성이 바뀌었으면 "마지막 판정 절 뒤 · `스캔 대상은` 문단 앞"이라는
위치 관계를 기준으로 삼는다.

아래 두 번째 블록(`### CLONE_ALLOWLIST`)은 문서에 이미 `## allowlist 추가 기준`
(`JSON_CALL_ALLOWLIST` 설명) 절이 있으면 그 절 안으로 옮겨 붙여도 된다. 어느 쪽이든
본문은 그대로 쓴다.

```markdown
### 중복 헬퍼 지표의 판정 파이프라인

측정 코어는 `scripts/_design_ratchet_clones.py` 다(스캐너 자신도 500줄 한도를 지켜야
해서 `_design_ratchet_scan.py` 에서 분리했다).

| 단계 | 규칙 |
|---|---|
| 1. 후보 | **데코레이터가 하나도 없는** 함수/메서드. 라우터·celery task·`property` 가 전부 배제된다 |
| 2. 메커니컬 | 본문에 `MECHANICAL_EXCLUDED_NAMES`(db·session·requests·settings·logger·self·cls …) / `MECHANICAL_EXCLUDED_ATTRS`(query·commit·execute …) 가 없고 `global`/`nonlocal` 이 없음 |
| 3. 크기 | AST 노드 수 ≥ `CLONE_MIN_AST_NODES`(20) |
| 4. 정규화 | 변수·인자명 → 등장순 `v0,v1,…` · docstring/데코레이터/annotation/반환타입 제거 · **상수 리터럴은 보존** |
| 5. 그룹핑 | 정규화 본문의 sha256 앞 12자가 동일 |

`open`·`Path`·`json`·`datetime` 은 **일부러 배제하지 않는다**. 얇은 메커니컬 I/O
래퍼(`_write_json` 류)가 실제 중복의 최대 밀도 구간이라 대상에 포함해야 한다.

상수 리터럴을 보존하므로 `round(x, 2)` 와 `round(x, 3)` 은 다른 그룹이다(보수적 판정).
반대로 annotation 을 제거하므로 타입만 다른 쌍은 같은 그룹이 된다(제네릭 통합 후보가
맞으므로 의도된 동작).

**미탐 범위(한정).** 이 지표도 총량 측정이 아니라 증가 차단 래칫이다.

- **의미적 유사** — 구조가 다르면서 같은 일을 하는 함수는 잡히지 않는다.
- **부분 통합** — 4곳 중 1곳만 제거하면 남은 3곳이 각 1을 유지해 지표가 줄지 않는다.
  위반은 아니지만(증가가 아니므로) 개선이 baseline 에 반영되지도 않는다.
- **클래스·타입·상수의 중복** — 함수만 스캔한다.
- **20 AST 노드 미만** — `_average` 류 소형 중복은 임계값 아래라 계수되지 않는다.

### `CLONE_ALLOWLIST`

통합하면 안 되는 그룹의 면제 목록이다. 키는 **멤버 신원**(`"파일:함수"` 정렬 후 `|`
결합)이지 콘텐츠 해시가 아니다. 해시로 잡으면 면제된 함수를 사소하게 고칠 때마다 키가
바뀌어 빌드가 깨진다. 멤버 신원이면 본문 수정은 통과하고, 복사본이 하나 더 생기면 멤버
집합이 달라져 면제가 풀린다.

등재됐지만 현재 스캔에 더 이상 없는 항목은 `python scripts/design_ratchet.py` 가
실패시킨다. 죽은 항목을 방치하면 면제 범위가 조용히 넓어지기 때문이다.
```

- [ ] **Step 6: 문서 정합 확인**

Run:
```bash
cd /home/deploy/project/bid-vector-dup-spec && \
grep -n "duplicate_mechanical_helpers" CLAUDE.md docs/operations/design-ratchet.md && \
grep -c "### 8. 중복 금지" CLAUDE.md && \
grep -n "design_ratchet.py" CLAUDE.md
```
Expected: `CLAUDE.md` 에 §4.5-8 블록 1개, 지표명 언급 2건 이상, §9 체크리스트에
`scripts/design_ratchet.py` 1건. `docs/operations/design-ratchet.md` 에 지표표 2행 +
판정 절.

- [ ] **Step 7: 전체 회귀 실행**

Run:
```bash
cd /home/deploy/project/bid-vector-dup-spec && \
/home/deploy/project/bid-vector/.venv/bin/ruff check \
  app/ scripts/design_ratchet.py scripts/_design_ratchet_scan.py \
  scripts/_design_ratchet_contracts.py scripts/_design_ratchet_clones.py && \
/home/deploy/project/bid-vector/.venv/bin/mypy app/ && \
/home/deploy/project/bid-vector/.venv/bin/pytest -q tests/
```
Expected: ruff `All checks passed!` · mypy 통과 · pytest 전부 통과

동시 실행 중인 다른 에이전트가 있으면 pytest 전체 스위트가 매우 느릴 수 있다(격리는
되어 있다). 오래 걸리면 `-q tests/test_design_ratchet.py tests/test_design_ratchet_clones.py`
로 먼저 좁혀 확인한 뒤 전체를 돌린다.

- [ ] **Step 8: 커밋**

```bash
cd /home/deploy/project/bid-vector-dup-spec && \
git add CLAUDE.md docs/operations/design-ratchet.md && \
git commit -m "docs(quality): 중복 금지 룰(§4.5-8) + 허브 주소표 + 래칫 지표 문서화

룰은 §4.5-6 에 이미 있었지만 '어디에 두는가'와 '무엇이 막는가'가 없어
scripts/_common.py 통합 이후에도 중복이 다시 자랐다. 주소표와 게이트
참조를 붙이고, 상수·캐스트만 다른 반복은 파라미터화된 해석기 + 얇은
명명 래퍼로 쓰도록 명시한다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## PR 마무리

- [ ] **push + PR 생성**

```bash
cd /home/deploy/project/bid-vector-dup-spec && \
git push -u origin docs/code-duplication-consolidation-spec
```

PR 본문에 포함할 것:
- 무엇을: 중복 헬퍼 지표 2개 신설 + baseline 동결 + §4.5-8 룰
- 왜: `scripts/_common.py` 가 스스로 "byte-for-byte 중복을 중앙화한 모듈"이라 밝히는데
  그 이후 `_write_json`×4 등이 다시 자랐다 — 문서 룰만으로는 유지되지 않는다
- 어떻게 테스트했는지: Task 4 Step 6 의 CLI 출력, Task 5 Step 4 의 probe 실증,
  Task 6 Step 7 의 ruff·mypy·pytest 결과
- 실제 baseline 수치 (Task 5 Step 2 출력)
- 연결: 스펙 `docs/superpowers/specs/2026-08-01-code-duplication-consolidation-design.md`
  §4·§6·§7, PR1/5
- 수용 기준: 기존 7개 지표 불변 · 신규 2지표 동결 · 죽은 allowlist 0 · CI green

**사용자가 명시적으로 머지를 승인하기 전에는 머지하지 않는다.**

---

## Self-Review

**1. 스펙 커버리지**

| 스펙 절 | 커버 |
|---|---|
| §4.1 지표 2축 정의 | Task 2(그룹핑) · Task 3(contracts 의 `FileMetrics` 2필드) |
| §4.2 판정 파이프라인 5단계 | Task 1(1~4단계) · Task 2(5단계) |
| §4.3 2-pass 아키텍처 · 계약 보존 | Task 3 Step 5·6 |
| §4.4 래칫 동작 검증 | Task 5 Step 4 (probe 실증) |
| §4.5 튜닝 표면 고정 · allowlist 멤버 신원 키 | Task 2 Step 3 · Task 1 픽스처 |
| §5 통합 작업 | **PR1 범위 밖** — PR2·PR3·PR5 |
| §6.1 §4.5-8 신설 | Task 6 Step 2 |
| §6.2 §4.5-6 주소표 | Task 6 Step 1 |
| §6.3 §9 체크리스트 · design-ratchet.md | Task 6 Step 3·4·5 |
| §7.1 양방향 픽스처 | Task 1 Step 1 · Task 2 Step 1 |
| §7.3 파싱 실패 · 죽은 allowlist | 기존 `RatchetScanError` 유지 · Task 4 |
| §7.4 성공 기준 | Task 5 Step 2 (실측 기록) |
| §8 미탐 범위 | Task 6 Step 5 |

§5(통합)와 프론트(§5.4)는 이 플랜의 범위가 아니다 — 스펙 §9 의 PR1 정의와 일치한다.

스펙 §7.4 의 수치는 재측정으로 갱신됐다: 인벤토리 교차파일 31그룹/71함수 · 동일파일
5그룹/10함수 → allowlist 2그룹(4함수) 제외 후 baseline 기대치 **교차 67 · 동일 10**
(Task 5 Step 2). 통과 조건이 아니라 sanity 기대치다.

**2. Placeholder 스캔** — "TBD"/"나중에"/"적절히 처리" 없음. 모든 코드 스텝에 실제
코드 블록이 있고, 모든 실행 스텝에 명령과 기대 출력이 있다.

**3. 타입 일관성** — Task 1 이 만든 `CloneSignature`(필드 `file`·`function`·`digest`·
`node_count`, 프로퍼티 `member`)를 Task 2 가 `_sig` 헬퍼와 `_build_group` 에서 같은
이름으로 쓰고, Task 3 이 `scan_file` 반환 타입
`tuple[FileMetrics, list[CloneSignature]]` 에서, Task 4 가 `scan_repo_with_signatures`
반환 타입에서 같게 쓴다. `CloneGroup`(필드 `members`·`files`·`node_count`, 프로퍼티
`key`)은 Task 2 정의 → Task 4 `format_clone_groups` 소비로 일치. 두 모델은 baseline
JSON 으로 직렬화되지 않는 내부 자료구조라 contracts 가 아니라
`_design_ratchet_clones.py` 에 산다(contracts 의 경계 = 직렬화되는 계약 + 순수 비교).
`count_cross_file_clones -> dict[str, int]` 는 value 가 스칼라라
`dict_boundary_functions` 지표에 잡히지 않는다(스캐너 자신의 위반 0 유지).

---

Plan complete and saved to
`docs/superpowers/plans/2026-08-01-duplicate-helper-ratchet-pr1.md`.

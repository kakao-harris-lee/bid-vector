"""SBERT prototype CategoryClassifierService + schedule."""

from __future__ import annotations

import pytest
import numpy as np

from app.models.models import Project  # noqa: F401 — ensures Base.metadata is populated
from app.tasks.celery_app import (
    RECLASSIFY_CATEGORIES_TASK_NAME,
    build_category_reclassify_beat_schedule,
)


class _FakeModel:
    """Deterministic 4-dim 'model' for tests. Maps known phrases to fixed vectors."""

    def __init__(self):
        # 4-D vectors per category-like keyword. Tests pick mid-vectors carefully.
        self.vocab = {
            "construction": np.array([1.0, 0.0, 0.0, 0.0]),
            "service": np.array([0.0, 1.0, 0.0, 0.0]),
            "software": np.array([0.0, 0.0, 1.0, 0.0]),
            "goods": np.array([0.0, 0.0, 0.0, 1.0]),
        }

    def encode(self, texts, normalize_embeddings: bool = True):
        out = []
        for t in texts:
            v = np.zeros(4)
            for kw, vec in self.vocab.items():
                if kw in t:
                    v = v + vec
            if not v.any():
                v = np.array([0.25, 0.25, 0.25, 0.25])
            if normalize_embeddings:
                norm = np.linalg.norm(v) or 1.0
                v = v / norm
            out.append(v)
        return np.asarray(out)


class _FakeModel384:
    """384-dim deterministic model for DB-backed tests.

    Each category keyword maps to a basis vector in the 384-dim space so that
    cosine similarity tests remain trivial while satisfying pgvector's dimension
    constraint.
    """

    # Map category name → index of the non-zero position (0–3).
    _CAT_IDX = {
        "construction": 0,
        "service": 1,
        "software": 2,
        "goods": 3,
    }
    DIM = 384

    def encode(self, texts, normalize_embeddings: bool = True):
        out = []
        for t in texts:
            v = np.zeros(self.DIM)
            for kw, idx in self._CAT_IDX.items():
                if kw in t:
                    v[idx] += 1.0
            if not v.any():
                v[:] = 1.0 / self.DIM
            if normalize_embeddings:
                norm = np.linalg.norm(v) or 1.0
                v = v / norm
            out.append(v)
        return np.asarray(out)

    @classmethod
    def basis_vector(cls, category: str) -> list[float]:
        """Return a normalised 384-dim basis vector for the given category."""
        v = np.zeros(cls.DIM)
        idx = cls._CAT_IDX[category]
        v[idx] = 1.0
        return v.tolist()


# ---------------------------------------------------------------------------
# Schedule tests
# ---------------------------------------------------------------------------


def test_reclassify_schedule_empty_by_default(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "CATEGORY_RECLASSIFY_SCHEDULE_ENABLED", False)
    assert build_category_reclassify_beat_schedule() == {}


def test_reclassify_schedule_builds(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "CATEGORY_RECLASSIFY_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "CATEGORY_RECLASSIFY_INTERVAL_MINUTES", 10)
    monkeypatch.setattr(settings, "CATEGORY_RECLASSIFY_BATCH_LIMIT", 75)
    entry = build_category_reclassify_beat_schedule()["category_reclassify_periodic"]
    assert entry["task"] == RECLASSIFY_CATEGORIES_TASK_NAME
    assert entry["schedule"] == 10 * 60
    assert entry["kwargs"]["limit"] == 75


# ---------------------------------------------------------------------------
# infer_category unit tests (4-dim fake model, no DB)
# ---------------------------------------------------------------------------


def test_infer_category_confident_match():
    from app.services.category_classifier import CategoryClassifierService

    svc = CategoryClassifierService(
        model=_FakeModel(),
        prototypes={
            "construction": ["construction"],
            "service": ["service"],
            "software": ["software"],
        },
    )
    # Project embedding aligned with "construction"
    cat, sim = svc.infer_category([1.0, 0.0, 0.0, 0.0], min_similarity=0.5, margin=0.1)
    assert cat == "construction"
    assert sim > 0.9


def test_infer_category_low_confidence_returns_none():
    from app.services.category_classifier import CategoryClassifierService

    svc = CategoryClassifierService(
        model=_FakeModel(),
        prototypes={
            "construction": ["construction"],
            "service": ["service"],
        },
    )
    # 50/50 between two categories — margin too small
    cat, sim = svc.infer_category([0.7071, 0.7071, 0.0, 0.0], min_similarity=0.3, margin=0.2)
    assert cat is None


def test_infer_category_no_model_returns_none():
    from app.services.category_classifier import CategoryClassifierService

    # No model override AND classifier returns None
    svc = CategoryClassifierService(model=None, prototypes={"construction": ["c"]})
    # Force _resolve_model to return None
    svc._resolve_model = lambda: None
    cat, sim = svc.infer_category([1.0, 0, 0, 0])
    assert cat is None


# ---------------------------------------------------------------------------
# DB-backed tests (384-dim to satisfy pgvector constraint in SQLite tests)
# ---------------------------------------------------------------------------


def test_reclassify_pending_updates_general_rows(test_db):
    from app.models.models import Project
    from app.services.category_classifier import CategoryClassifierService

    # software-aligned 384-dim embedding (index 2 = 1.0, rest 0)
    software_vec = _FakeModel384.basis_vector("software")

    p = Project(
        title="AI 플랫폼 구축 용역",
        category="general",
        status="open",
        embedding=software_vec,
        semantic_text="ignored",
    )
    test_db.add(p)
    test_db.commit()

    svc = CategoryClassifierService(
        model=_FakeModel384(),
        prototypes={
            "construction": ["construction"],
            "service": ["service"],
            "software": ["software"],
            "goods": ["goods"],
        },
    )
    stats = svc.reclassify_pending(test_db, limit=10)
    assert stats["candidates"] == 1
    assert stats["updated"] == 1
    test_db.refresh(p)
    assert p.category == "software"


def test_match_title_keyword_software_construction_goods():
    """Title regex shortcuts map common Korean keywords to category."""
    from app.services.category_classifier import CategoryClassifierService

    svc = CategoryClassifierService(model=None)  # model not needed for keyword path
    assert svc.match_title_keyword("수소기술사업화 지원 플랫폼 구축") == "software"
    assert svc.match_title_keyword("AI 데이터 분석 시스템 고도화") == "software"
    assert svc.match_title_keyword("1차 냉각펌프기기실 로컬에어쿨러 수리") == "goods"
    assert svc.match_title_keyword("의료기기 구매 입찰") == "goods"
    assert svc.match_title_keyword("나운1동 도로정비공사 폐기물처리용역") == "technical-service" or \
        svc.match_title_keyword("나운1동 도로정비공사 폐기물처리용역") == "construction"
    assert svc.match_title_keyword("학교 위탁용역 운영") == "service"
    # Fallback when no keyword matches
    assert svc.match_title_keyword("아무 의미 없는 텍스트") is None
    assert svc.match_title_keyword(None) is None


def test_match_title_keyword_repair_does_not_match_compound_korean_nouns():
    """REGRESSION: '수리' must not match compound nouns like '수리시설' / '개방수리'.

    Production id=20054 was misclassified to goods because '수리시설정비사업'
    contains '수리'. Tighten boundary so '수리' only matches when adjacent
    chars are NOT Hangul (i.e., standalone Korean word).
    """
    from app.services.category_classifier import CategoryClassifierService

    svc = CategoryClassifierService(model=None)
    # Compound nouns — must NOT match goods
    assert svc.match_title_keyword("2026년 옥천군 수리시설정비사업 폐기물처리용역") == "service"
    # "개방수리" — '방' before '수리' is Hangul → no match → falls through
    assert svc.match_title_keyword("함정 구명벌 개방수리 단가 계약") != "goods"
    # Standalone '수리' — SHOULD match goods
    assert svc.match_title_keyword("냉각펌프 수리") == "goods"
    assert svc.match_title_keyword("분석기 수리 (긴급)") == "goods"


def test_reclassify_uses_keyword_path_without_model(test_db):
    """Keyword shortcut works even when SBERT model is unavailable."""
    from app.models.models import Project
    from app.services.category_classifier import CategoryClassifierService

    p1 = Project(title="냉각펌프 수리", category="general", status="open")
    p2 = Project(title="SW사업 적합사업 기준 개발", category="general", status="open")
    p3 = Project(title="아무런 키워드도 없는 행", category="general", status="open")
    test_db.add_all([p1, p2, p3])
    test_db.commit()

    # model=None — no SBERT prototypes. Keyword path still fires.
    svc = CategoryClassifierService(model=None)
    svc._resolve_model = lambda: None  # force no model
    stats = svc.reclassify_pending(test_db, limit=10)

    assert stats["candidates"] == 3
    assert stats["updated_from_keyword"] == 2
    assert stats["updated_from_sbert"] == 0
    assert stats["skipped_model_unavailable"] == 1
    assert stats["updated"] == 2  # legacy alias

    test_db.refresh(p1)
    test_db.refresh(p2)
    test_db.refresh(p3)
    assert p1.category == "goods"
    assert p2.category == "software"
    assert p3.category == "general"  # unchanged


def test_reclassify_skips_when_already_specific(test_db):
    from app.models.models import Project
    from app.services.category_classifier import CategoryClassifierService

    construction_vec = _FakeModel384.basis_vector("construction")
    p = Project(
        title="이미 분류됨",
        category="construction",
        status="open",
        embedding=construction_vec,
    )
    test_db.add(p)
    test_db.commit()

    svc = CategoryClassifierService(model=_FakeModel384())
    stats = svc.reclassify_pending(test_db, limit=10)
    assert stats["candidates"] == 0
    assert stats["updated"] == 0

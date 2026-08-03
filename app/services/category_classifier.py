"""SBERT prototype-based category classifier.

Re-assigns Project.category for rows stuck at 'general' or 'other' via:
  1. Fast title-keyword regex pass — high-precision phrases (e.g. "공사",
     "수리", "SW", "플랫폼") map directly without SBERT.
  2. SBERT cosine similarity against precomputed per-category prototype
     embeddings, gated by min_similarity + margin.

Falls back silently if the SBERT model is unavailable (offline / dev).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass


class CategoryClassifierService:
    """Re-assign Project.category for rows stuck at 'general'/'other' using SBERT cosine sim.

    Uses precomputed prototype embeddings (one mean vector per category).
    Falls back silently if SBERT model isn't loadable (offline / dev).
    """

    DEFAULT_PROTOTYPES: dict[str, list[str]] = {
        "construction": [
            "건축공사 신축",
            "토목공사 도로",
            "전기공사 설비",
            "증축 공사 감리",
            "건설공사 발주",
            "건축물 보수",
            "조경 공사",
        ],
        "service": [
            "위탁용역 운영",
            "일반용역 관리",
            "체험학습 운영 용역",
            "교육 행사 운영",
            "정책 연구 위탁",
            "행정 지원 용역",
        ],
        "technical-service": [
            "기술용역 감리",
            "엔지니어링 기술 검토",
            "기술지원 컨설팅",
            "공사감리 용역",
        ],
        "software": [
            "정보시스템 구축",
            "소프트웨어 개발 용역",
            "SW 시스템 유지관리",
            "AI 플랫폼 구축",
            "데이터 분석 시스템 고도화",
            "웹 서비스 개발",
        ],
        "goods": [
            "물품 구매 입찰",
            "장비 구매",
            "기자재 구매",
            "사무기기 구매",
            "차량 구매",
            "의료기기 구매",
        ],
    }

    # High-precision title regex shortcuts. Ordered specific → generic.
    # First match wins. Falls through to SBERT only when no regex matches.
    DEFAULT_TITLE_KEYWORDS: list[tuple[str, str]] = [
        # software — strong technical indicators
        (r"소프트웨어|SW사업|시스템\s*구축|플랫폼\s*구축|정보시스템|"
         r"앱\s*개발|웹\s*서비스|데이터\s*분석.*시스템|시스템.*고도화|AI\s*플랫폼",
         "software"),
        # goods — purchase / repair / equipment
        # "수리" uses Korean word-like boundary (no 한글 neighbor) so it catches
        # standalone "분석기 수리" / "에어쿨러 수리" but skips compound nouns
        # like "수리시설" (irrigation facility) / "개방수리" (annexed verb).
        (r"물품\s*구매|장비\s*구매|기자재\s*구매|구매\s*입찰|"
         r"기기\s*구매|차량\s*구매|의료기기|(?<![가-힣])수리(?![가-힣])|장비\s*도입",
         "goods"),
        # technical-service — engineering oversight
        (r"기술용역|엔지니어링|공사\s*감리|감리\s*용역|기술지원|"
         r"정밀안전점검|기술지도|건설사업관리",
         "technical-service"),
        # construction — bare 공사 (already-tagged compound forms handled by predictor band)
        (r"공사", "construction"),
        # service — broadest fallback within keyword pass
        (r"용역|위탁|운영\s*및|체험학습|홍보\s*용역|영상\s*제작|콘텐츠\s*제작",
         "service"),
    ]

    def __init__(self, model=None, prototypes=None, title_keywords=None):
        self._model_override = model
        self._prototype_texts: dict[str, list[str]] = prototypes or self.DEFAULT_PROTOTYPES
        self._prototype_vectors: dict[str, list[float]] | None = None
        raw_keywords = title_keywords if title_keywords is not None else self.DEFAULT_TITLE_KEYWORDS
        self._title_keyword_rules: list[tuple[re.Pattern[str], str]] = []
        for pattern, category in raw_keywords:
            try:
                self._title_keyword_rules.append((re.compile(pattern), category))
            except re.error:
                continue

    def match_title_keyword(self, title: str | None) -> str | None:
        """Return the first matching category for the title, or None."""
        text = str(title or "").strip()
        if not text or not self._title_keyword_rules:
            return None
        for pattern, category in self._title_keyword_rules:
            if pattern.search(text):
                return category
        return None

    def _ensure_prototypes(self) -> dict[str, list[float]]:
        """Lazy-build prototype vectors. Returns dict or empty if model unavailable."""
        if self._prototype_vectors is not None:
            return self._prototype_vectors

        model = self._resolve_model()
        if model is None:
            self._prototype_vectors = {}
            return self._prototype_vectors

        import numpy as np

        result: dict[str, list[float]] = {}
        for cat, phrases in self._prototype_texts.items():
            vectors = model.encode(phrases, normalize_embeddings=True)
            mean = np.asarray(vectors).mean(axis=0)
            norm = float(np.linalg.norm(mean)) or 1.0
            result[cat] = (mean / norm).tolist()

        self._prototype_vectors = result
        return self._prototype_vectors

    def _resolve_model(self):
        """Return the embedding model to use, or None if unavailable."""
        if self._model_override is not None:
            return self._model_override

        try:
            from app.services.classifier import NoticeClassifierService

            return NoticeClassifierService._get_embedding_model()
        except Exception:
            return None

    def infer_category(
        self,
        project_embedding,
        *,
        min_similarity: float = 0.30,
        margin: float = 0.04,
    ) -> tuple[str | None, float]:
        """Return (category, similarity) or (None, best_sim) if not confident enough.

        Confident = best_sim >= min_similarity AND best_sim - second_best >= margin.
        """
        prototypes = self._ensure_prototypes()
        if not prototypes or not project_embedding:
            return (None, 0.0)

        import numpy as np

        emb = np.asarray(project_embedding, dtype=float)
        emb_norm = float(np.linalg.norm(emb)) or 1.0
        emb = emb / emb_norm

        sims: list[tuple[str, float]] = []
        for cat, proto_vec in prototypes.items():
            proto = np.asarray(proto_vec, dtype=float)
            sims.append((cat, float(np.dot(emb, proto))))

        sims.sort(key=lambda x: x[1], reverse=True)
        best_cat, best_sim = sims[0]
        second_sim = sims[1][1] if len(sims) > 1 else 0.0

        if best_sim < min_similarity or (best_sim - second_sim) < margin:
            return (None, best_sim)

        return (best_cat, best_sim)

    def reclassify_pending(self, db, *, limit: int = 100) -> dict:
        """Update Project.category for rows where category IN ('general', 'other').

        Order:
          1. Title regex shortcut (high-precision phrases) — fires regardless of
             SBERT model availability.
          2. SBERT cosine sim (confidence-gated) for rows the regex didn't match.
        """
        from app.models.models import Project
        from app.services.similarity_read_model import invalidate_project_embedding

        @dataclass
        class _Stats:
            candidates: int = 0
            updated_from_keyword: int = 0
            updated_from_sbert: int = 0
            skipped_no_embedding: int = 0
            skipped_low_confidence: int = 0
            skipped_model_unavailable: int = 0

        stats = _Stats()
        updated_project_ids: list[int] = []

        prototypes = self._ensure_prototypes()
        model_available = bool(prototypes)

        candidates = (
            db.query(Project)
            .filter(Project.category.in_(["general", "other"]))
            .order_by(Project.id.desc())
            .limit(limit)
            .all()
        )
        stats.candidates = len(candidates)

        for project in candidates:
            # 1. Title regex shortcut — works even without SBERT.
            keyword_match = self.match_title_keyword(project.title)
            if keyword_match is not None:
                project.category = keyword_match
                invalidate_project_embedding(project)
                db.add(project)
                updated_project_ids.append(int(project.id))
                stats.updated_from_keyword += 1
                continue

            # 2. SBERT path requires both an embedding and a working model.
            if not model_available:
                stats.skipped_model_unavailable += 1
                continue

            embedding = project.embedding
            if embedding is None:
                stats.skipped_no_embedding += 1
                continue
            try:
                vec = list(embedding)
            except Exception:
                stats.skipped_no_embedding += 1
                continue

            best, _ = self.infer_category(vec)
            if best is None:
                stats.skipped_low_confidence += 1
                continue

            project.category = best
            invalidate_project_embedding(project)
            db.add(project)
            updated_project_ids.append(int(project.id))
            stats.updated_from_sbert += 1

        if stats.updated_from_keyword or stats.updated_from_sbert:
            db.commit()

        result = asdict(stats)
        result["updated_project_ids"] = updated_project_ids
        # legacy alias for any consumer reading a single "updated" field
        result["updated"] = stats.updated_from_keyword + stats.updated_from_sbert
        return result

"""Forward paper-bidding run 구현 (진행 중 공고, 생성 시점 정산 없음).

``orchestration.py`` 는 두 run 의 **공개 진입점**(celery/JSON 경계)과 historical
replay 구현을 맡고, 이 모듈은 forward 경로의 구현만 맡는다. 두 경로는 요청 스냅샷 키
집합·데이터 컷오프 정책·정산 유무가 전부 다른 별개의 책임이라, orchestration.py 가
파일 크기 한도(§4.5-4)에 걸린 시점에 이 축으로 분해했다. 메서드 본문은 이동 전과
동일하고, 실행 시각(``data_cutoff_at``)은 진입점에서 **주입**받는다(§4.7-3) — 내부는
시간 전역 호출이 없어 값 테이블로 검증된다.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from sqlalchemy.orm import Session

from app.core.constants import FORWARD_PAPER_RUN_MODE
from app.models.models import (
    CompanyProfile,
    OperatorStrategy,
    PaperBidRun,
    Project,
)
from app.schemas.paper_bidding import PaperBiddingRunExecutionResponse
from app.schemas.paper_bidding_items import PaperBiddingCandidateItem
from app.schemas.paper_bidding_runs import (
    ForwardPaperBiddingRunRequestSnapshot,
    ForwardPaperRunParams,
)
from app.services.paper_bidding_backtest.base import _PaperBiddingBase

logger = logging.getLogger(__name__)

# forward paper run 의 "실제 투찰 후보"로 세는 action (skip 은 제외). 정산 창이 없어
# ``settle_actions`` 를 받지 않으므로 이 집합이 forward 경로의 고정 기준이다.
FORWARD_PAPER_BID_ACTIONS: frozenset[str] = frozenset({"bid_now", "review"})


@dataclass(frozen=True)
class _PreparedForwardRun:
    """Resolved + normalized inputs for one forward paper run.

    ``_prepare_forward_run`` 이 만든다(운영자/전략/프로필 해석, 요청 스냅샷, 영속화된
    ``PaperBidRun``). historical 경로의 ``_PreparedHistoricalRun`` 과 같은 패턴이며,
    ``_create_run`` 은 execute ``try/except`` **밖**에서 돌아 생성 실패가
    ``_fail_run`` 없이 그대로 전파된다(이동 전과 동일).
    """

    run: PaperBidRun | None
    request_payload: ForwardPaperBiddingRunRequestSnapshot
    operator_id: int
    strategy: OperatorStrategy
    profile: CompanyProfile | None
    data_cutoff_at: datetime


class _ForwardRunMixin(_PaperBiddingBase):
    """Forward paper-bidding run implementation (no settlement at generation time)."""

    def _run_forward_paper_bidding(
        self,
        db: Session,
        *,
        params: ForwardPaperRunParams,
        data_cutoff_at: datetime,
    ) -> PaperBiddingRunExecutionResponse:
        """Generate paper bids for currently open/re-notice projects."""
        prepared = self._prepare_forward_run(
            db, params=params, data_cutoff_at=data_cutoff_at
        )
        candidate_items: list[PaperBiddingCandidateItem] = []
        action_counts: Counter[str] = Counter()
        try:
            projects = self._load_forward_projects(
                db,
                category=params.category,
                limit=params.limit,
                data_cutoff_at=data_cutoff_at,
            )
            skipped_by_strategy, skipped_invalid = self._process_forward_projects(
                db,
                prepared=prepared,
                params=params,
                projects=projects,
                candidate_items=candidate_items,
                action_counts=action_counts,
            )
            return self._complete_forward_paper_run(
                db,
                prepared=prepared,
                params=params,
                candidate_items=candidate_items,
                skipped_by_strategy=skipped_by_strategy,
                skipped_invalid=skipped_invalid,
                action_counts=action_counts,
            )
        except Exception as exc:
            self._fail_run(
                db, run=prepared.run, persist=params.persist, error_message=str(exc)
            )
            raise

    def _prepare_forward_run(
        self,
        db: Session,
        *,
        params: ForwardPaperRunParams,
        data_cutoff_at: datetime,
    ) -> _PreparedForwardRun:
        """Resolve/normalize inputs and open the ``PaperBidRun`` for the forward run."""
        operator = self._resolve_operator(db, operator_id=params.operator_id)
        strategy = self._resolve_operator_strategy(db, operator=operator)
        profile = self._resolve_operator_profile(db, operator=operator)
        request_payload = ForwardPaperBiddingRunRequestSnapshot(
            category=params.category,
            limit=params.limit,
            scenario=params.scenario,
            strategy_version=params.strategy_version,
            model_version=params.model_version,
            history_limit=params.history_limit,
            persist=params.persist,
            data_cutoff_at=data_cutoff_at.isoformat(),
        )
        run = self._create_run(
            db,
            operator_id=int(operator.id),
            request_payload=request_payload,
            persist=params.persist,
            category=params.category,
            scenario=params.scenario,
            strategy_version=params.strategy_version,
            model_version=params.model_version,
            start_at=None,
            end_at=None,
            cutoff_hours_before_deadline=0,
            mode=FORWARD_PAPER_RUN_MODE,
        )
        return _PreparedForwardRun(
            run=run,
            request_payload=request_payload,
            operator_id=int(operator.id),
            strategy=strategy,
            profile=profile,
            data_cutoff_at=data_cutoff_at,
        )

    def _process_forward_projects(
        self,
        db: Session,
        *,
        prepared: _PreparedForwardRun,
        params: ForwardPaperRunParams,
        projects: Sequence[Project],
        candidate_items: list[PaperBiddingCandidateItem],
        action_counts: Counter[str],
    ) -> tuple[int, int]:
        """Build + persist a paper bid per project. Returns (skipped_by_strategy, skipped_invalid)."""
        skipped_by_strategy = 0
        skipped_invalid = 0
        for project in projects:
            if not self._passes_strategy(project, prepared.strategy):
                skipped_by_strategy += 1
                continue
            item = self._build_forward_candidate_or_none(
                db, prepared=prepared, params=params, project=project
            )
            if item is None:
                skipped_invalid += 1
                continue
            action_counts[item.action] += 1
            candidate_items.append(item)
            self._persist_paper_bid(
                db,
                run=prepared.run,
                operator_id=prepared.operator_id,
                item=item,
                persist=params.persist,
                model_version=params.model_version,
                strategy_version=params.strategy_version,
            )
        return skipped_by_strategy, skipped_invalid

    def _build_forward_candidate_or_none(
        self,
        db: Session,
        *,
        prepared: _PreparedForwardRun,
        params: ForwardPaperRunParams,
        project: Project,
    ) -> PaperBiddingCandidateItem | None:
        """후보 1건 생성. 단일 공고가 망가져 있으면 ``None`` (run 전체를 죽이지 않는다)."""
        try:
            return self._build_candidate_item(
                db,
                project=project,
                tender_result=None,
                data_cutoff_at=prepared.data_cutoff_at,
                scenario=params.scenario,
                strategy_version=params.strategy_version,
                cutoff_hours_before_deadline=0,
                history_limit=params.history_limit,
                profile=prepared.profile,
            )
        except ValueError as project_exc:
            # A single malformed project (e.g. 0 budget for ebiz4u-link imports)
            # must not abort the whole run — skip + count it.
            logger.warning(
                "forward_paper: skipping project %s due to %s",
                getattr(project, "id", "?"),
                project_exc,
            )
            return None

    def _complete_forward_paper_run(
        self,
        db: Session,
        *,
        prepared: _PreparedForwardRun,
        params: ForwardPaperRunParams,
        candidate_items: list[PaperBiddingCandidateItem],
        skipped_by_strategy: int,
        skipped_invalid: int,
        action_counts: Counter[str],
    ) -> PaperBiddingRunExecutionResponse:
        summary = self._build_summary(
            candidate_items=candidate_items,
            settlement_items=[],
            skipped_by_strategy=skipped_by_strategy,
            action_counts=action_counts,
            skipped_invalid=skipped_invalid,
        )
        run = prepared.run
        self._complete_run(
            db,
            run=run,
            persist=params.persist,
            summary=summary,
            candidate_count=len(candidate_items),
            paper_bid_count=sum(
                1 for item in candidate_items if item.action in FORWARD_PAPER_BID_ACTIONS
            ),
            settled_count=0,
        )
        return PaperBiddingRunExecutionResponse(
            run_id=int(run.id) if run is not None and run.id is not None else None,
            request=prepared.request_payload,
            summary=summary,
            items=candidate_items,
            settlements=[],
        )

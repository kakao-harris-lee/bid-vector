"""``PaperBidAction`` / ``PriceScenario`` 어휘 단일 출처 drift 가드.

투찰 액션 3값(``bid_now``/``review``/``skip``)과 가격 시나리오 3값
(``conservative``/``base``/``aggressive``)은 라우터 · 요청/응답 스키마 · task payload ·
beat 스케줄 · 서비스 정규화기에 걸쳐 쓰인다. 종전에는 각 파일이 같은 값 집합을
``Literal`` 이나 ``set`` 으로 **다시 선언**해서(§4.5-1 위반) 한 곳만 고쳐도 나머지가
조용히 갈라졌다. 이제 ``app/core/constants.py`` 가 단일 출처이고, 이 모듈은 셋을 고정한다.

1. 단일 출처의 **값과 순서**(기본값·직렬화 순서·CLI 선택지가 여기에 붙어 있다).
2. 각 사용처 필드가 그 별칭을 실제로 쓰는지(주석을 ``get_args`` 로 되짚어 확인).
3. ``app/`` · ``scripts/`` 어디에서도 같은 값 집합을 **다시 선언하지 않는지**(AST 스캔)
   — 재중복이 생기면 여기서 실패한다.
4. 그 AST 탐지기 자체가 각 선언 형태를 실제로 잡는지(자기검증). 탐지기가 조용히 부식하면
   3번 가드는 항상 통과하는 장식이 되므로, 인라인 소스 값 테이블로 형태별 검출을 고정한다.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

import pytest
from pydantic import BaseModel

from app.api.synthetic import SyntheticBacktestRunRequest
from app.core.constants import (
    PAPER_BID_ACTIONS,
    PRICE_SCENARIOS,
    PaperBidAction,
    PriceScenario,
)
from app.schemas.accuracy import RecommendationFeedbackLabelItem
from app.schemas.dashboard import DashboardOpportunityItem
from app.schemas.decision import (
    DecisionExperimentOutcome,
    DecisionFunnelRecentSubmissionItem,
    DecisionInsightsRecentItem,
)
from app.schemas.operator import OperatorDashboardDecisionItem
from app.schemas.operator_strategy import (
    OperatorStrategyCandidateItem,
    OperatorStrategyMonitorResultItem,
)
from app.schemas.opportunity import (
    BidDecisionRecordResponse,
    BidDecisionResponse,
)
from app.schemas.paper_bidding import (
    ForwardPaperBiddingRunRequest,
    PaperBiddingRunRequest,
)
from app.schemas.paper_bidding_items import PaperBiddingCandidateItem
from app.schemas.prediction import PricePredictionScenario
from app.schemas.synthetic import (
    SyntheticExperimentParams,
    SyntheticExperimentSampleGapRunReference,
)
from app.schemas.task_payloads import (
    HistoricalBacktestTaskRequest,
    SyntheticOperatorBacktestTaskRequest,
)
from app.schemas.telegram import TelegramActionResponse
from app.services.dashboard_summary.constants import _PAPER_ACTION_STATUS

REPO_ROOT = Path(__file__).resolve().parents[2]
# 어휘 재선언을 스캔할 소스 트리. ``scripts/`` 는 CLI ``choices`` 로 어휘를 복제하기 쉬운
# 사각지대라 함께 순회한다(§4.5-1 은 앱/스크립트를 구분하지 않는다).
SCANNED_DIRS: tuple[str, ...] = ("app", "scripts")
# 교차 모듈 어휘의 단일 출처.
VOCABULARY_SOURCE = "app/core/constants.py"
# 한 모듈 안에서만 쓰이는 어휘는 그 모듈이 소유자다.
DECISION_SCHEMA_SOURCE = "app/schemas/decision.py"


def _literal_value_sets(annotation: Any) -> set[tuple[Any, ...]]:
    """주석 안에 (중첩까지) 등장하는 모든 ``Literal`` 값 묶음.

    ``Union[bool, List[PaperBidAction]]`` 처럼 감싼 형태에서도 어휘를 꺼내야 하므로
    ``get_args`` 로 재귀한다.
    """
    if get_origin(annotation) is Literal:
        return {get_args(annotation)}
    found: set[tuple[Any, ...]] = set()
    for arg in get_args(annotation):
        found |= _literal_value_sets(arg)
    return found


def _field_literals(model: type[BaseModel], field_name: str) -> set[tuple[Any, ...]]:
    assert (
        field_name in model.model_fields
    ), f"{model.__name__} 에 {field_name} 필드가 없습니다 — 어휘 사용처 표를 갱신하세요."
    return _literal_value_sets(model.model_fields[field_name].annotation)


def _string_elements(nodes: list[ast.expr]) -> tuple[str, ...] | None:
    """모두 문자열 상수면 그 값들, 아니면 ``None``."""
    if not nodes or not all(
        isinstance(node, ast.Constant) and isinstance(node.value, str) for node in nodes
    ):
        return None
    return tuple(node.value for node in nodes)  # type: ignore[attr-defined]


def _is_literal_subscript(node: ast.AST) -> bool:
    """``Literal[...]`` / ``typing.Literal[...]`` 첨자 표현인지."""
    if not isinstance(node, ast.Subscript):
        return False
    base = node.value
    name = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", "")
    return name == "Literal"


def _declared_vocabularies(source: str) -> list[tuple[str, ...]]:
    """소스가 문자열 값 집합을 직접 선언한 지점 전체.

    ``Literal["a", "b"]`` (타입 어휘)와 ``{"a", "b"}`` / ``("a", "b")`` / ``["a", "b"]``
    (멤버십·기본값·CLI choices 집합)을 함께 센다. 둘 다 어휘를 복제하는 형태다.

    ``dict`` **키**는 세지 않는다: 액션 -> 문구/상태 룩업표(§4.5-2)는 어휘를 복제하는 게
    아니라 어휘로 인덱싱하는 선언적 데이터이고, 키를 상수로 바꿀 수단도 없다. 그 완전성은
    아래 ``test_paper_action_status_map_covers_every_action`` 처럼 표별로 고정한다.

    미커버(의도): 리터럴이 아닌 우회 표현 — ``"a b c".split()``, f-string 조립,
    런타임 계산 집합. AST 상수만 보므로 이런 형태로 어휘를 복제하면 잡지 못한다.
    """
    tree = ast.parse(source)
    # ``Literal["a", "b"]`` 의 slice 는 그 자체가 ``Tuple`` 이라 아래 튜플 분기에서 한 번 더
    # 잡힌다. 같은 선언을 두 번 보고하지 않도록 slice 노드를 미리 제외한다.
    literal_slices = {
        id(node.slice) for node in ast.walk(tree) if _is_literal_subscript(node)
    }
    declared: list[tuple[str, ...]] = []
    for node in ast.walk(tree):
        elements: tuple[str, ...] | None = None
        if _is_literal_subscript(node):
            slice_node = node.slice  # type: ignore[union-attr]
            elements = _string_elements(
                slice_node.elts if isinstance(slice_node, ast.Tuple) else [slice_node]
            )
        elif (
            isinstance(node, (ast.Set, ast.Tuple, ast.List))
            and id(node) not in literal_slices
        ):
            elements = _string_elements(list(node.elts))
        if elements is not None:
            declared.append(elements)
    return declared


def _scanned_modules() -> list[Path]:
    return sorted(
        path
        for directory in SCANNED_DIRS
        for path in (REPO_ROOT / directory).rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _redeclaring_paths(vocabulary: tuple[str, ...]) -> list[str]:
    """어휘를 직접 선언한 소스 경로(레포 상대) 목록."""
    expected = set(vocabulary)
    return sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for path in _scanned_modules()
        if any(
            set(declared) == expected
            for declared in _declared_vocabularies(path.read_text(encoding="utf-8"))
        )
    )


class TestVocabularySingleSource:
    """단일 출처의 값과 순서를 고정한다."""

    def test_paper_bid_actions_are_pinned(self) -> None:
        # 순서는 계약이다: 기본값(``["bid_now"]``)과 직렬화/선택지 순서가 여기에 붙어 있다.
        assert PAPER_BID_ACTIONS == ("bid_now", "review", "skip")
        assert get_args(PaperBidAction) == PAPER_BID_ACTIONS

    def test_price_scenarios_are_pinned(self) -> None:
        assert PRICE_SCENARIOS == ("conservative", "base", "aggressive")
        assert get_args(PriceScenario) == PRICE_SCENARIOS

    def test_paper_action_status_map_covers_every_action(self) -> None:
        """대시보드 action -> decision_status 룩업이 어휘 전체를 덮는지 확인한다.

        키가 빠지면 그 액션이 조용히 기본값(``reviewing``)으로 접혀 잘못된 상태가 표시된다.
        """
        assert set(_PAPER_ACTION_STATUS) == set(PAPER_BID_ACTIONS)


class TestActionVocabularyConsumers:
    """액션 어휘를 쓰는 필드가 모두 같은 값 묶음을 갖는지 고정한다."""

    FIELDS = [
        pytest.param(RecommendationFeedbackLabelItem, "action", id="feedback_label"),
        pytest.param(DashboardOpportunityItem, "action", id="dashboard_opportunity"),
        pytest.param(DecisionInsightsRecentItem, "action", id="decision_recent"),
        pytest.param(
            DecisionFunnelRecentSubmissionItem, "initial_action", id="funnel_initial"
        ),
        pytest.param(
            DecisionFunnelRecentSubmissionItem, "current_action", id="funnel_current"
        ),
        pytest.param(OperatorDashboardDecisionItem, "action", id="operator_dashboard"),
        pytest.param(OperatorStrategyCandidateItem, "action", id="strategy_candidate"),
        pytest.param(
            OperatorStrategyMonitorResultItem, "action", id="strategy_monitor"
        ),
        pytest.param(BidDecisionResponse, "action", id="bid_decision"),
        pytest.param(BidDecisionRecordResponse, "action", id="bid_decision_record"),
        pytest.param(
            BidDecisionRecordResponse, "initial_action", id="bid_record_initial"
        ),
        pytest.param(TelegramActionResponse, "action", id="telegram_action"),
        pytest.param(PaperBiddingCandidateItem, "action", id="paper_bid_candidate"),
        pytest.param(PaperBiddingRunRequest, "settle_actions", id="historical_request"),
        pytest.param(
            SyntheticExperimentParams, "settle_actions", id="experiment_params"
        ),
        pytest.param(
            SyntheticExperimentSampleGapRunReference,
            "settle_actions",
            id="sample_gap_run_reference",
        ),
        pytest.param(
            SyntheticBacktestRunRequest, "settle_actions", id="synthetic_http_request"
        ),
        pytest.param(
            SyntheticOperatorBacktestTaskRequest, "settle_actions", id="synthetic_task"
        ),
        pytest.param(
            HistoricalBacktestTaskRequest, "settle_actions", id="historical_task"
        ),
    ]

    @pytest.mark.parametrize(("model", "field_name"), FIELDS)
    def test_field_uses_the_single_source_vocabulary(
        self, model: type[BaseModel], field_name: str
    ) -> None:
        assert PAPER_BID_ACTIONS in _field_literals(model, field_name), (
            f"{model.__name__}.{field_name} 이 {VOCABULARY_SOURCE} 의 PaperBidAction 을 "
            "쓰지 않습니다."
        )


class TestScenarioVocabularyConsumers:
    """예측기 라벨과 run 요청의 시나리오가 같은 어휘인지 고정한다.

    백테스트는 요청 ``scenario`` 문자열을 예측 산출의 ``label`` 과 문자열 비교로 맞춘다
    (``_select_scenario``). 두 어휘가 갈라지면 후보가 조용히 0건이 된다.
    """

    FIELDS = [
        pytest.param(PricePredictionScenario, "label", id="predictor_label"),
        pytest.param(PaperBiddingRunRequest, "scenario", id="historical_request"),
        pytest.param(ForwardPaperBiddingRunRequest, "scenario", id="forward_request"),
        pytest.param(HistoricalBacktestTaskRequest, "scenario", id="historical_task"),
    ]

    @pytest.mark.parametrize(("model", "field_name"), FIELDS)
    def test_field_uses_the_single_source_vocabulary(
        self, model: type[BaseModel], field_name: str
    ) -> None:
        assert PRICE_SCENARIOS in _field_literals(model, field_name), (
            f"{model.__name__}.{field_name} 이 {VOCABULARY_SOURCE} 의 PriceScenario 을 "
            "쓰지 않습니다."
        )


class TestNoRedeclaration:
    """``app/`` · ``scripts/`` 안에서 같은 값 집합을 다시 선언하면 실패한다."""

    VOCABULARIES = [
        pytest.param(PAPER_BID_ACTIONS, VOCABULARY_SOURCE, id="paper_bid_action"),
        pytest.param(PRICE_SCENARIOS, VOCABULARY_SOURCE, id="price_scenario"),
        # 한 모듈 전용 어휘(종전 같은 파일 안에서 5번 선언)도 같은 가드로 묶는다.
        pytest.param(
            get_args(DecisionExperimentOutcome),
            DECISION_SCHEMA_SOURCE,
            id="decision_experiment_outcome",
        ),
    ]

    @pytest.mark.parametrize(("vocabulary", "owner"), VOCABULARIES)
    def test_only_the_owner_declares_the_vocabulary(
        self, vocabulary: tuple[str, ...], owner: str
    ) -> None:
        offenders = _redeclaring_paths(vocabulary)
        assert offenders == [owner], (
            f"{sorted(vocabulary)} 어휘가 {owner} 밖에서 다시 선언되었습니다: "
            f"{[path for path in offenders if path != owner]}. "
            "별칭(또는 값 튜플)을 import 해서 쓰세요."
        )


# 탐지기 자기검증 값 테이블: (소스, 기대 검출 묶음들).
# 형태별 분기를 하나라도 지우면 해당 케이스가 실패해야 한다.
SCANNER_CASES = [
    pytest.param(
        'from typing import Literal\nX = Literal["a", "b", "c"]\n',
        [("a", "b", "c")],
        id="literal_alias",
    ),
    pytest.param(
        'X: str = "a"\nY = Literal["a"]\n', [("a",)], id="literal_single_value"
    ),
    pytest.param(
        'import typing\nX = typing.Literal["a", "b"]\n',
        [("a", "b")],
        id="literal_qualified_attribute",
    ),
    pytest.param('VALUES = {"a", "b", "c"}\n', [("a", "b", "c")], id="set_literal"),
    pytest.param('VALUES = ("a", "b", "c")\n', [("a", "b", "c")], id="tuple_literal"),
    pytest.param('VALUES = ["a", "b", "c"]\n', [("a", "b", "c")], id="list_literal"),
    pytest.param(
        'choices = list(["a", "b"])\n', [("a", "b")], id="list_inside_call_choices"
    ),
    pytest.param('LABELS = {"a": "가", "b": "나"}\n', [], id="dict_keys_not_counted"),
    pytest.param('VALUES = ("a", 1)\n', [], id="mixed_types_not_counted"),
    pytest.param("VALUES = ()\n", [], id="empty_not_counted"),
    pytest.param('VALUES = "a b c".split()\n', [], id="split_call_not_covered"),
]


class TestVocabularyScanner:
    """AST 탐지기가 각 선언 형태를 실제로 잡는지 고정한다(가드의 가드)."""

    @pytest.mark.parametrize(("source", "expected"), SCANNER_CASES)
    def test_detects_declaration_shape(
        self, source: str, expected: list[tuple[str, ...]]
    ) -> None:
        assert _declared_vocabularies(source) == expected

    def test_nested_shapes_are_all_reported(self) -> None:
        """한 파일에 여러 형태가 섞여 있어도 각각 보고한다(부분 검출 금지)."""
        source = (
            "from typing import Literal\n"
            'ACTIONS = Literal["a", "b"]\n'
            'FALLBACK = {"a", "b"}\n'
            'LABELS = {"a": 1, "b": 2}\n'
        )
        assert _declared_vocabularies(source) == [("a", "b"), ("a", "b")]

    def test_owner_lookup_uses_the_scanner(self) -> None:
        """실제 트리 스캔이 단일 출처를 집어내는지(경로 배선 확인)."""
        assert _redeclaring_paths(PAPER_BID_ACTIONS) == [VOCABULARY_SOURCE]
        assert _redeclaring_paths(PRICE_SCENARIOS) == [VOCABULARY_SOURCE]

    def test_scanned_tree_covers_app_and_scripts(self) -> None:
        scanned = {path.relative_to(REPO_ROOT).parts[0] for path in _scanned_modules()}
        assert scanned == set(SCANNED_DIRS)

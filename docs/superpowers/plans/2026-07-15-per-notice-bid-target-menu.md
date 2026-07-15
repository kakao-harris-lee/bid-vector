# Per-notice 투찰가 메뉴 (bid target menu) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 발주처 밴드 안에서 공고별 신호로 추천 투찰가를 위치시키고, 리스크별 3종(추천/공격/안전) 투찰가 메뉴를 예측 출력에 첨부한다 (백엔드만).

**Architecture:** 순수 모듈 `app/ai/bid_target.py`가 `(밴드 floor/ceiling, 사업금액 base, 신호) → 메뉴 dict`를 만든다. 서비스 `app/services/bid_target_signals.py`가 과거 낙찰률 산포를 SQL 집계로 조회해 신호를 만든다. `opportunity_analysis`/`prediction_workflow`가 predict_price 결과의 밴드 + 신호로 메뉴를 조립해 `prediction["bid_target_menu"]`에 첨부하고 `recommended_amount`를 메뉴 '추천'으로 정렬한다.

**Tech Stack:** Python 3.12, SQLAlchemy(집계 `func.stddev`), Pydantic v2 스키마, pytest.

## Global Constraints

- 투찰 base는 **사업금액(기초금액, `HistoricalData.base_amount`)** — `predict_price`가 이미 그 budget으로 계산(PR #162). 메뉴 가격도 동일 base × rate.
- **정직 명세(§2)**: 승률/낙하확률 수치 금지. `basis`는 사실만, `caveat` 필수(예정가격 비공개·투찰서 초안).
- guardrail 밴드 값/캘리브레이션 **무변경**(PR #149 소유). 메뉴는 밴드 위에 얹는 additive 레이어.
- **65535 교훈**: 산포는 row 로드가 아니라 SQL 집계(`func.stddev`+`func.count`)로.
- 파일 크기 소프트 한도(§4.5): 함수 ~50줄, 파일 ~500줄.
- MVP 신호 = **과거 낙찰률 산포 단독**. 경쟁 프록시·복수예비가격은 범위 밖.

---

### Task 1: 순수 모듈 `bid_target` — 신호 dataclass + 위치조정 + 메뉴 빌더

**Files:**
- Create: `app/ai/bid_target.py`
- Test: `tests/test_bid_target.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class BidTargetSignals: win_rate_dispersion: float | None; data_sufficient: bool`
  - `build_bid_target_menu(*, floor_bid_rate: float | None, ceiling_bid_rate: float | None, budget: float | None, signals: BidTargetSignals | None) -> dict | None`
  - Module constants `CAVEAT: str`.

- [ ] **Step 1: Write failing tests for the menu builder**

Create `tests/test_bid_target.py`:

```python
from app.ai.bid_target import BidTargetSignals, build_bid_target_menu, CAVEAT


def _labels(menu):
    return [o["label"] for o in menu["options"]]


def test_menu_has_three_options_floor_and_ceiling_fixed():
    menu = build_bid_target_menu(
        floor_bid_rate=0.8806, ceiling_bid_rate=0.882, budget=88_042_000,
        signals=BidTargetSignals(win_rate_dispersion=None, data_sufficient=False),
    )
    assert _labels(menu) == ["recommended", "aggressive", "safe"]
    opts = {o["label"]: o for o in menu["options"]}
    assert opts["aggressive"]["bid_rate"] == 0.8806          # floor
    assert opts["safe"]["bid_rate"] == 0.882                 # ceiling
    assert 0.8806 <= opts["recommended"]["bid_rate"] <= 0.882


def test_prices_use_business_amount_base():
    menu = build_bid_target_menu(
        floor_bid_rate=0.8806, ceiling_bid_rate=0.882, budget=88_042_000,
        signals=BidTargetSignals(win_rate_dispersion=None, data_sufficient=False),
    )
    opts = {o["label"]: o for o in menu["options"]}
    assert opts["aggressive"]["bid_price"] == round(88_042_000 * 0.8806, 2)
    assert opts["safe"]["bid_price"] == round(88_042_000 * 0.882, 2)


def test_insufficient_signal_anchors_recommended_near_floor():
    menu = build_bid_target_menu(
        floor_bid_rate=0.8806, ceiling_bid_rate=0.882, budget=88_042_000,
        signals=BidTargetSignals(win_rate_dispersion=None, data_sufficient=False),
    )
    rec = {o["label"]: o for o in menu["options"]}["recommended"]["bid_rate"]
    # base adjustment 0.15 → near floor, well below mid (0.8813)
    assert 0.8806 < rec < 0.8813


def test_high_dispersion_moves_recommended_toward_safe():
    low = build_bid_target_menu(
        floor_bid_rate=0.8806, ceiling_bid_rate=0.882, budget=88_042_000,
        signals=BidTargetSignals(win_rate_dispersion=0.001, data_sufficient=True),
    )
    high = build_bid_target_menu(
        floor_bid_rate=0.8806, ceiling_bid_rate=0.882, budget=88_042_000,
        signals=BidTargetSignals(win_rate_dispersion=0.05, data_sufficient=True),
    )
    rec = lambda m: {o["label"]: o for o in m["options"]}["recommended"]["bid_rate"]
    assert rec(high) > rec(low)


def test_no_band_returns_none():
    assert build_bid_target_menu(
        floor_bid_rate=None, ceiling_bid_rate=None, budget=88_042_000,
        signals=BidTargetSignals(win_rate_dispersion=None, data_sufficient=False),
    ) is None


def test_collapsed_band_marks_collapsed_and_equal_rates():
    menu = build_bid_target_menu(
        floor_bid_rate=0.90, ceiling_bid_rate=0.90, budget=88_042_000,
        signals=BidTargetSignals(win_rate_dispersion=None, data_sufficient=False),
    )
    assert menu["collapsed"] is True
    assert {o["bid_rate"] for o in menu["options"]} == {0.90}


def test_zero_budget_yields_none_prices_but_rates_present():
    menu = build_bid_target_menu(
        floor_bid_rate=0.8806, ceiling_bid_rate=0.882, budget=0,
        signals=BidTargetSignals(win_rate_dispersion=None, data_sufficient=False),
    )
    assert all(o["bid_price"] is None for o in menu["options"])
    assert all(o["bid_rate"] is not None for o in menu["options"])


def test_honesty_no_probability_fields_and_caveat_present():
    menu = build_bid_target_menu(
        floor_bid_rate=0.8806, ceiling_bid_rate=0.882, budget=88_042_000,
        signals=BidTargetSignals(win_rate_dispersion=None, data_sufficient=False),
    )
    assert menu["caveat"] == CAVEAT
    blob = str(menu).lower()
    assert "확률" not in blob and "probability" not in blob and "승률" not in blob
    for o in menu["options"]:
        assert o["basis"] and o["stance"] and o["risk_note"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_bid_target.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.ai.bid_target'`).

- [ ] **Step 3: Implement `app/ai/bid_target.py`**

```python
"""Pure builder for the per-notice 투찰가 메뉴 (bid target menu).

Given the agency guardrail band [floor, ceiling] (a % of the notice's
사업금액/기초금액), the notice's base amount, and a small signal set, produce a
3-option menu: recommended (per-notice positioned within the band), aggressive
(band floor — most competitive, 낙하 위험), safe (band ceiling — safe from 낙하,
less competitive).

Honesty (CLAUDE.md §2): options carry qualitative stance + factual basis, never
win/낙하 probabilities. The true 낙찰하한 depends on the 복수예비가격 추첨
(개찰 전 비공개), so ``CAVEAT`` states this is decision support, not a guarantee.
"""
from __future__ import annotations

from dataclasses import dataclass

# recommended anchor: 15% up from the floor → near the competitive 최저적격 target.
_BASE_ADJUSTMENT = 0.15
# winning-rate std at which dispersion fully lifts the recommended toward mid.
_DISPERSION_REFERENCE = 0.02
# never let the signal alone push the recommended past this fraction of the band.
_MAX_ADJUSTMENT = 0.85

CAVEAT = (
    "예정가격은 복수예비가격 추첨(개찰 전 비공개)으로 결정되어 정확한 낙찰하한을 "
    "보장할 수 없습니다. 이 값들은 투찰서 초안·의사결정 지원이며 KONEPS 자동 제출·"
    "확정 낙찰이 아닙니다."
)


@dataclass(frozen=True)
class BidTargetSignals:
    """Per-notice signals that position the recommended bid within the band."""

    win_rate_dispersion: float | None
    data_sufficient: bool


def _resolve_position_adjustment(signals: BidTargetSignals | None) -> float:
    """Fraction in [0, 1] of the band width above the floor for the recommended bid.

    Anchored near the floor (competitive 최저적격 target); more past winning-rate
    dispersion means more 예정가격 uncertainty, so the recommended nudges toward
    the safer ceiling. Insufficient data keeps the neutral base anchor.
    """
    if signals is None or not signals.data_sufficient or signals.win_rate_dispersion is None:
        return _BASE_ADJUSTMENT
    dispersion = max(0.0, float(signals.win_rate_dispersion))
    lift = min(1.0, dispersion / _DISPERSION_REFERENCE) * (0.5 - _BASE_ADJUSTMENT)
    return max(0.0, min(_MAX_ADJUSTMENT, _BASE_ADJUSTMENT + lift))


def _price(budget: float | None, rate: float) -> float | None:
    if budget is None or float(budget) <= 0:
        return None
    return round(float(budget) * rate, 2)


def _build_signals_summary(signals: BidTargetSignals | None, adjustment: float) -> str:
    if signals is None or not signals.data_sufficient or signals.win_rate_dispersion is None:
        return "과거 낙찰률 산포 데이터 부족 → 추천을 밴드 하한 근처(기본값)에 배치했습니다."
    return (
        f"과거 낙찰률 산포 {signals.win_rate_dispersion:.3%}를 반영해 추천을 "
        f"밴드 하한에서 {adjustment:.0%} 지점에 배치했습니다."
    )


def build_bid_target_menu(
    *,
    floor_bid_rate: float | None,
    ceiling_bid_rate: float | None,
    budget: float | None,
    signals: BidTargetSignals | None,
) -> dict | None:
    """Return a 3-option bid target menu, or None when there is no agency band."""
    if floor_bid_rate is None or ceiling_bid_rate is None:
        return None
    floor = float(floor_bid_rate)
    ceiling = float(ceiling_bid_rate)
    collapsed = ceiling <= floor + 1e-9
    adjustment = _resolve_position_adjustment(signals)
    recommended = floor if collapsed else floor + adjustment * (ceiling - floor)
    recommended = min(max(recommended, floor), ceiling)

    band_note = f"발주처 밴드 {floor:.2%}~{ceiling:.2%}"
    options = [
        {
            "label": "recommended",
            "stance": "신호 종합 균형",
            "bid_rate": recommended,
            "bid_price": _price(budget, recommended),
            "risk_note": "공고별 신호로 밴드 내 배치한 균형 투찰가입니다.",
            "basis": band_note,
        },
        {
            "label": "aggressive",
            "stance": "경쟁력 높음 · 낙하 위험 있음",
            "bid_rate": floor,
            "bid_price": _price(budget, floor),
            "risk_note": "밴드 하한(최저적격 경쟁 타겟). 실현 낙찰하한이 더 높으면 낙(실격) 위험.",
            "basis": band_note,
        },
        {
            "label": "safe",
            "stance": "낙하 위험 낮음 · 경쟁력 낮음",
            "bid_rate": ceiling,
            "bid_price": _price(budget, ceiling),
            "risk_note": "밴드 상한. 낙 위험은 낮지만 더 낮게 투찰한 적격자에게 밀릴 수 있음.",
            "basis": band_note,
        },
    ]
    return {
        "options": options,
        "band_floor_rate": floor,
        "band_ceiling_rate": ceiling,
        "signals_summary": _build_signals_summary(signals, adjustment),
        "caveat": CAVEAT,
        "collapsed": collapsed,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_bid_target.py -q`
Expected: PASS (9 passed).

- [ ] **Step 5: Lint + commit**

Run: `/home/deploy/project/bid-vector/.venv/bin/ruff check app/ai/bid_target.py tests/test_bid_target.py`
Expected: All checks passed.

```bash
git add app/ai/bid_target.py tests/test_bid_target.py
git commit -m "feat(bid-target): pure 투찰가 메뉴 builder (band + signals -> 3 options)"
```

---

### Task 2: 신호 리졸버 — 과거 낙찰률 산포 (SQL 집계)

**Files:**
- Create: `app/services/bid_target_signals.py`
- Test: `tests/test_bid_target_signals.py`

**Interfaces:**
- Consumes: `app.ai.bid_target.BidTargetSignals`
- Produces: `resolve_bid_target_signals(db: Session, *, agency_name: str | None, category: str | None, window_days: int = 365, min_samples: int = 8) -> BidTargetSignals`

- [ ] **Step 1: Write failing tests**

Create `tests/test_bid_target_signals.py`:

```python
from datetime import timedelta

from app.ai.bid_target import BidTargetSignals
from app.core.time import utc_now
from app.models.models import Project, TenderResult
from app.services.bid_target_signals import resolve_bid_target_signals


def _seed(db, agency, category, rates):
    now = utc_now()
    for i, rate in enumerate(rates):
        p = Project(title=f"n{i}", category=category, issuing_agency=agency, budget_estimate=1000.0)
        db.add(p)
        db.flush()
        db.add(TenderResult(
            project_id=p.id, winning_company="w", winning_amount=880.0,
            winning_rate=rate, announced_at=now - timedelta(days=10),
        ))
    db.commit()


def test_insufficient_samples_marks_not_sufficient(test_db):
    _seed(test_db, "한국수산자원공단동해본부", "service", [0.88, 0.89, 0.90])  # < min_samples
    sig = resolve_bid_target_signals(test_db, agency_name="한국수산자원공단동해본부", category="service")
    assert isinstance(sig, BidTargetSignals)
    assert sig.data_sufficient is False
    assert sig.win_rate_dispersion is None


def test_sufficient_samples_returns_stddev(test_db):
    rates = [0.86, 0.88, 0.90, 0.87, 0.89, 0.885, 0.895, 0.875, 0.905, 0.865]
    _seed(test_db, "한국수산자원공단동해본부", "service", rates)
    sig = resolve_bid_target_signals(test_db, agency_name="한국수산자원공단동해본부", category="service")
    assert sig.data_sufficient is True
    assert sig.win_rate_dispersion is not None and sig.win_rate_dispersion > 0


def test_other_agency_not_counted(test_db):
    _seed(test_db, "서울특별시", "service", [0.86, 0.88, 0.90, 0.87, 0.89, 0.885, 0.895, 0.875, 0.905, 0.865])
    sig = resolve_bid_target_signals(test_db, agency_name="한국수산자원공단동해본부", category="service")
    assert sig.data_sufficient is False
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_bid_target_signals.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `app/services/bid_target_signals.py`**

```python
"""Resolve per-notice signals for the bid target menu from historical outcomes.

MVP signal: dispersion (population stddev) of realized winning rates for the
same issuing agency + category over a recent window. Computed as a SQL
aggregate (never loading rows) to stay well under the bind-parameter limit
(see prediction_feedback chunking incident).
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.ai.bid_target import BidTargetSignals
from app.core.time import utc_now
from app.models.models import Project, TenderResult


def resolve_bid_target_signals(
    db: Session,
    *,
    agency_name: str | None,
    category: str | None,
    window_days: int = 365,
    min_samples: int = 8,
) -> BidTargetSignals:
    if not agency_name:
        return BidTargetSignals(win_rate_dispersion=None, data_sufficient=False)
    date_from = utc_now() - timedelta(days=window_days)
    query = (
        db.query(
            func.stddev_pop(TenderResult.winning_rate),
            func.count(TenderResult.id),
        )
        .join(Project, Project.id == TenderResult.project_id)
        .filter(
            Project.issuing_agency == agency_name,
            TenderResult.winning_rate > 0,
            TenderResult.announced_at >= date_from,
        )
    )
    if category:
        query = query.filter(Project.category == category)
    dispersion, sample_count = query.one()
    if sample_count is None or sample_count < min_samples or dispersion is None:
        return BidTargetSignals(win_rate_dispersion=None, data_sufficient=False)
    return BidTargetSignals(win_rate_dispersion=float(dispersion), data_sufficient=True)
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_bid_target_signals.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Lint + commit**

Run: `/home/deploy/project/bid-vector/.venv/bin/ruff check app/services/bid_target_signals.py tests/test_bid_target_signals.py`
Expected: All checks passed.

```bash
git add app/services/bid_target_signals.py tests/test_bid_target_signals.py
git commit -m "feat(bid-target): win-rate dispersion signal resolver (SQL aggregate)"
```

---

### Task 3: 스키마 — BidTargetOption / BidTargetMenu + PricePredictionResponse

**Files:**
- Modify: `app/schemas/schemas.py`
- Test: `tests/test_bid_target_schema.py`

**Interfaces:**
- Produces: pydantic `BidTargetOption`, `BidTargetMenu`; `PricePredictionResponse.bid_target_menu: Optional[BidTargetMenu]`.

- [ ] **Step 1: Write failing test**

Create `tests/test_bid_target_schema.py`:

```python
from app.ai.bid_target import BidTargetSignals, build_bid_target_menu
from app.schemas.schemas import BidTargetMenu, PricePredictionResponse


def test_menu_dict_validates_as_schema():
    menu = build_bid_target_menu(
        floor_bid_rate=0.8806, ceiling_bid_rate=0.882, budget=88_042_000,
        signals=BidTargetSignals(win_rate_dispersion=None, data_sufficient=False),
    )
    model = BidTargetMenu.model_validate(menu)
    assert len(model.options) == 3
    assert model.caveat


def test_price_prediction_response_accepts_menu():
    resp = PricePredictionResponse(
        predicted_price=1.0, price_range_min=1.0, price_range_max=1.0,
        confidence_score=0.5, model_version="v1",
        bid_target_menu=build_bid_target_menu(
            floor_bid_rate=0.8806, ceiling_bid_rate=0.882, budget=88_042_000,
            signals=BidTargetSignals(win_rate_dispersion=None, data_sufficient=False),
        ),
    )
    assert resp.bid_target_menu is not None
    assert resp.bid_target_menu.band_floor_rate == 0.8806
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_bid_target_schema.py -q`
Expected: FAIL (`ImportError: cannot import name 'BidTargetMenu'`).

- [ ] **Step 3: Add schemas to `app/schemas/schemas.py`**

Add near `PricePredictionScenario` (search for that class). Insert:

```python
class BidTargetOption(BaseModel):
    label: Literal["recommended", "aggressive", "safe"]
    stance: str
    bid_rate: float = Field(ge=0.0)
    bid_price: Optional[float] = Field(default=None, ge=0.0)
    risk_note: str
    basis: str


class BidTargetMenu(BaseModel):
    options: List[BidTargetOption]
    band_floor_rate: Optional[float] = Field(default=None, ge=0.0)
    band_ceiling_rate: Optional[float] = Field(default=None, ge=0.0)
    signals_summary: str
    caveat: str
    collapsed: bool = False
```

Then in `class PricePredictionResponse(BaseModel):` add the field (place it right after `bid_rate_candidates`):

```python
    bid_target_menu: Optional[BidTargetMenu] = None
```

(Confirm `Literal`, `List`, `Optional`, `Field`, `BaseModel` are already imported at the top of the file — they are used by neighboring classes.)

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_bid_target_schema.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Lint + commit**

Run: `/home/deploy/project/bid-vector/.venv/bin/ruff check app/schemas/schemas.py tests/test_bid_target_schema.py`
Expected: All checks passed.

```bash
git add app/schemas/schemas.py tests/test_bid_target_schema.py
git commit -m "feat(bid-target): BidTargetOption/Menu schema + PricePredictionResponse field"
```

---

### Task 4: 통합 — opportunity_analysis가 메뉴 조립 + recommended_amount 정렬 (★결정1)

**Files:**
- Modify: `app/services/opportunity_analysis.py` (`_build_price_prediction`, and where `recommended_amount` is resolved)
- Test: `tests/test_bid_target_integration.py`

**Interfaces:**
- Consumes: `app.services.bid_target_signals.resolve_bid_target_signals`, `app.ai.bid_target.build_bid_target_menu`.
- Produces: `prediction["bid_target_menu"]` on the analyze_project price_prediction; `recommended_amount` == menu recommended bid_price when a menu exists.

- [ ] **Step 1: Read the current shape**

Run: `sed -n '250,340p' app/services/opportunity_analysis.py` and locate `_build_price_prediction` (~L422) and `_resolve_recommended_amount` (grep it).
Run: `grep -n "_resolve_recommended_amount\|def _build_price_prediction\|resolve_notice_bid_base" app/services/opportunity_analysis.py`

- [ ] **Step 2: Write failing integration test**

Create `tests/test_bid_target_integration.py`:

```python
from app.core.single_user import ensure_operator_account
from app.models.models import HistoricalData, Project
from app.schemas.schemas import OpportunityAnalysisRequest
from app.services.opportunity_analysis import OpportunityAnalysisService


def _vat_notice(db, *, agency="한국수산자원공단동해본부", est=80_038_182, base=88_042_000):
    p = Project(title="동해바다숲", category="service", issuing_agency=agency, budget_estimate=est)
    db.add(p)
    db.flush()
    db.add(HistoricalData(project_id=p.id, base_amount=base, predicted_price=est, bid_rate=0.0))
    db.commit()
    return p


def test_analyze_project_attaches_bid_target_menu_on_business_amount_base(test_db, monkeypatch):
    # Ensure the agency band applies (한국수산자원공단 floor/ceiling from settings).
    p = _vat_notice(test_db)
    op = ensure_operator_account(test_db)
    req = OpportunityAnalysisRequest(project_id=p.id, agency_name=p.issuing_agency)
    res = OpportunityAnalysisService().analyze_project(test_db, p, req, operator=op)
    menu = (res.get("price_prediction") or {}).get("bid_target_menu")
    assert menu is not None
    opts = {o["label"]: o for o in menu["options"]}
    # aggressive == floor price on the 사업금액 base (88,042,000), not est.
    assert opts["aggressive"]["bid_price"] == round(88_042_000 * menu["band_floor_rate"], 2)
    # recommended_amount aligned to the menu recommended (★결정1).
    assert res["recommended_amount"] == opts["recommended"]["bid_price"]
```

- [ ] **Step 3: Run to verify fail**

Run: `pytest tests/test_bid_target_integration.py -q`
Expected: FAIL (`bid_target_menu` is None / KeyError).

- [ ] **Step 4: Wire the menu into `_build_price_prediction`**

At the top of `app/services/opportunity_analysis.py` add imports (next to the existing `from app.services.bid_base import resolve_notice_bid_base`):

```python
from app.ai.bid_target import build_bid_target_menu
from app.services.bid_target_signals import resolve_bid_target_signals
```

In `_build_price_prediction`, after the `predict_price(...)` result is obtained, build and attach the menu before returning. Change the `return (...)` block so it computes:

```python
        prediction = self.price_prediction_port.predict_price(
            budget=resolve_notice_bid_base(db, project),
            category=project.category or "other",
            description=f"{project.description or ''} {project.requirements or ''}".strip(),
            historical_records=self._load_price_history(db, project),
            agency_name=request.agency_name,
            feedback_calibration=feedback_calibration,
            business_type_code=business_type_code,
            business_group=business_group,
            legal_floor_bid_rate=request.legal_floor_bid_rate,
        )
        signals = resolve_bid_target_signals(
            db, agency_name=request.agency_name, category=project.category
        )
        menu = build_bid_target_menu(
            floor_bid_rate=prediction.get("floor_bid_rate"),
            ceiling_bid_rate=prediction.get("ceiling_bid_rate"),
            budget=resolve_notice_bid_base(db, project),
            signals=signals,
        )
        if menu is not None:
            prediction["bid_target_menu"] = menu
        return (prediction, business_group)
```

(If `_build_price_prediction` currently returns the `predict_price(...)` call inline, refactor to assign `prediction = ...` first, as shown.)

- [ ] **Step 5: Align `recommended_amount` to the menu recommended (★결정1)**

Find `_resolve_recommended_amount` (from Step 1). Add a menu-first branch: when `price_prediction.get("bid_target_menu")` exists, return its `recommended` option `bid_price`. Concretely, at the START of that method body:

```python
        menu = (price_prediction or {}).get("bid_target_menu")
        if menu:
            for option in menu.get("options", []):
                if option.get("label") == "recommended" and option.get("bid_price") is not None:
                    return float(option["bid_price"])
```

(Keep the existing logic below as the fallback when there is no menu.)

- [ ] **Step 6: Run integration + regression**

Run: `pytest tests/test_bid_target_integration.py tests/test_predictions.py tests/test_predictor_business_group.py -q`
Expected: PASS. If a prior snapshot test asserted `recommended_amount` at the ceiling for a 한국수산자원공단 notice, update it to the menu recommended (intended by ★결정1) and note it in the commit.

- [ ] **Step 7: Lint + commit**

Run: `/home/deploy/project/bid-vector/.venv/bin/ruff check app/services/opportunity_analysis.py tests/test_bid_target_integration.py`

```bash
git add app/services/opportunity_analysis.py tests/test_bid_target_integration.py
git commit -m "feat(bid-target): attach menu in opportunity_analysis + align recommended_amount"
```

---

### Task 5: 통합 — prediction_workflow (API /predictions/price)가 메뉴 첨부

**Files:**
- Modify: `app/services/prediction_workflow.py` (`predict_project_price`)
- Test: `tests/test_bid_target_workflow.py`

**Interfaces:**
- Consumes: `resolve_bid_target_signals`, `build_bid_target_menu`, `resolve_notice_bid_base`.
- Produces: `prediction["bid_target_menu"]` on the workflow prediction dict.

- [ ] **Step 1: Write failing test**

Create `tests/test_bid_target_workflow.py`:

```python
from app.models.models import HistoricalData, Project
from app.schemas.schemas import PricePredictionRequest
from app.services.prediction_workflow import PredictionWorkflowService


def test_workflow_prediction_includes_menu(test_db):
    p = Project(title="동해바다숲", category="service",
                issuing_agency="한국수산자원공단동해본부", budget_estimate=80_038_182)
    test_db.add(p)
    test_db.flush()
    test_db.add(HistoricalData(project_id=p.id, base_amount=88_042_000, predicted_price=80_038_182, bid_rate=0.0))
    test_db.commit()
    req = PricePredictionRequest(project_id=p.id, category="service", description="바다숲",
                                 agency_name="한국수산자원공단동해본부", budget_estimate=80_038_182)
    out = PredictionWorkflowService().predict_project_price(test_db, req)
    assert out.get("bid_target_menu") is not None
```

(Confirm `PricePredictionRequest` field names via `grep -n "class PricePredictionRequest" app/schemas/schemas.py` and adjust the constructor to the real required fields.)

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_bid_target_workflow.py -q`
Expected: FAIL (menu None).

- [ ] **Step 3: Wire into `predict_project_price`**

Add imports near `from app.services.bid_base import resolve_notice_bid_base`:

```python
from app.ai.bid_target import build_bid_target_menu
from app.services.bid_target_signals import resolve_bid_target_signals
```

After `prediction = self.price_prediction_port.predict_price(...)` and before the DB row is built/returned, add:

```python
        menu = build_bid_target_menu(
            floor_bid_rate=prediction.get("floor_bid_rate"),
            ceiling_bid_rate=prediction.get("ceiling_bid_rate"),
            budget=resolved_bid_base or request.budget_estimate,
            signals=resolve_bid_target_signals(
                db, agency_name=request.agency_name, category=request.category or project.category
            ),
        )
        if menu is not None:
            prediction["bid_target_menu"] = menu
```

(`resolved_bid_base` is the local already computed in Task/PR #162; if the variable name differs, recompute `resolve_notice_bid_base(db, project)`.)

- [ ] **Step 4: Run to verify pass + regression**

Run: `pytest tests/test_bid_target_workflow.py tests/test_prediction_api_decoupling.py -q`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

Run: `/home/deploy/project/bid-vector/.venv/bin/ruff check app/services/prediction_workflow.py tests/test_bid_target_workflow.py`

```bash
git add app/services/prediction_workflow.py tests/test_bid_target_workflow.py
git commit -m "feat(bid-target): attach menu in prediction_workflow (API path)"
```

---

### Task 6: OpenAPI 타입 sync + 전체 회귀

**Files:**
- Modify: `frontend/src/shared/types/openapi.d.ts` (generated)

- [ ] **Step 1: Regenerate OpenAPI types**

Run the sync-types skill flow (per CLAUDE.md §6): regenerate the backend OpenAPI schema and update `frontend/src/shared/types/openapi.d.ts`. Verify `BidTargetMenu` / `bid_target_menu` appear in the generated types.

- [ ] **Step 2: Full backend regression**

Run:
```
pytest tests/test_bid_target.py tests/test_bid_target_signals.py tests/test_bid_target_schema.py \
  tests/test_bid_target_integration.py tests/test_bid_target_workflow.py \
  tests/test_predictions.py tests/test_predictor_business_group.py \
  tests/test_prediction_api_decoupling.py tests/test_large_function_budgets.py \
  tests/test_bid_base.py -q
```
Expected: all PASS.

- [ ] **Step 3: Frontend type check (no runtime UI yet)**

Run: `npm --prefix frontend run build`
Expected: build succeeds (types compile with the new optional field).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/shared/types/openapi.d.ts
git commit -m "chore(bid-target): sync OpenAPI types for bid_target_menu"
```

---

## Self-Review

- **Spec coverage:** §4.1 pure module → Task 1; §4.2 signal resolver → Task 2; §5 schema → Task 3; §4.3 integration + ★결정1 → Tasks 4-5; §9 sync-types → Task 6; §6 honesty → asserted in Task 1 (`test_honesty_...`) and embedded strings; §8 tests → each task's tests. No uncovered section.
- **Placeholders:** none — every code step shows complete code; test bodies are concrete.
- **Type consistency:** `BidTargetSignals(win_rate_dispersion, data_sufficient)` used identically in Tasks 1/2/4/5; `build_bid_target_menu(floor_bid_rate, ceiling_bid_rate, budget, signals)` keyword-consistent across Tasks 1/4/5; menu dict keys (`options/band_floor_rate/band_ceiling_rate/signals_summary/caveat/collapsed`, option `label/stance/bid_rate/bid_price/risk_note/basis`) match the Task 3 schema field-for-field.
- **Deploy:** after merge, `docker compose --profile tasks restart api worker` (predict_price path), per #162 pattern.

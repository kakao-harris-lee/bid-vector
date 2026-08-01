"""JSON (de)serialization helpers for stored experiment payloads.

The **load** half delegates to the shared restore path
(:mod:`app.services.stored_json_payload`) so the decode + degrade policy lives in
one place. The **dump** half still calls ``json.dumps(default=str)`` on purpose:
``default=str`` is this module's serialization *contract* (non-JSON leaves such as
datetimes are lowered to ``str(value)``), and reproducing it through pydantic
requires a per-payload key contract for ``summary_json`` / ``metrics_json`` /
``breakdown_json`` first — otherwise the stored strings would change. That
promotion is left as a follow-up.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from app.services.stored_json_payload import load_stored_json_value

# degrade 경고에서 **어느 컬럼**이 해석 불가였는지 특정하는 라벨. 이 패키지가 소유하는
# JSON 컬럼의 단일 출처이고(§4.5-1), 같은 컬럼을 읽는 다른 패키지(analytics_reporting /
# bid_summary)도 리터럴을 다시 적지 않고 이 상수를 import 한다.
EXPERIMENT_PARAMS_COLUMN = "synthetic_experiment.params_json"
EXPERIMENT_OPERATOR_SLUGS_COLUMN = "synthetic_experiment.operator_slugs_json"
RUN_SUMMARY_COLUMN = "synthetic_experiment_run.summary_json"
RESULT_METRICS_COLUMN = "synthetic_experiment_result.metrics_json"
RESULT_SETTLEMENT_SAMPLE_COLUMN = "synthetic_experiment_result.settlement_sample_json"
RESULT_BREAKDOWN_COLUMN = "synthetic_experiment_result.breakdown_json"


def _json_dumps(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


def _json_loads(value: Optional[str], *, context: str = "") -> Any:
    """Restore a stored experiment payload (object *or* array), ``None`` if unreadable.

    Callers keep their own degrade policy (``_json_loads(...) or {}`` /
    ``or []``) and pass the **column label** they are reading (``context``) so a
    degrade warning names the actual column instead of a generic placeholder. An
    unreadable payload is warned about by the shared restore path with that label,
    never with the payload text.
    """
    return load_stored_json_value(value, context=context)


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None

"""Notice-classification focus modules (decomposed from ``classifier.py``).

The public surface — ``NoticeClassifierService`` plus the module-level
``is_construction_project`` / ``detect_regional_preference`` /
``REGION_PREFERENCE_PATTERN`` helpers — stays in ``app.services.classifier``.
These modules hold the stateless scoring logic that surface delegates to:

* ``config`` — declarative scoring weights, penalties, and tier-band ladders.
* ``taxonomy`` — business-type / region / license reference data.
* ``assessment`` — the shared ``RuleAssessment`` result contract.
* ``text`` — pure text/normalization helpers (no scoring).
* ``region`` — 지역 signal detection and the region axis.
* ``eligibility`` — business-type and license (hard categorical) axes.
* ``budget`` — budget and capability (financial capacity) axes.
* ``semantic`` — semantic-similarity axis and its text builders.

The split is behaviour-preserving: the axis functions are the original
``NoticeClassifierService`` method bodies with ``self.<CONST>`` rewritten to the
``config``/``taxonomy`` module constant and ``self.<helper>`` to the module
function. Output is byte-for-byte identical.
"""

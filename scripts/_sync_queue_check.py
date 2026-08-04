"""Exact worker/queue topology gate used by ``scripts/sync-after-merge.sh``.

The gate answers one question before beat is allowed back up: does the running
Celery cluster consume exactly the queue sets docker-compose declares, and is
every scheduled task registered on the nodes consuming the queue it routes to?

A union check is not enough. If the ops worker also consumed the ML inference
queue, the union of all nodes would still contain every configured queue while
per-worker isolation (the memory boundary from #317/#321) was already broken.

Reads a JSON payload on stdin combining both celery inspect calls::

    {"registered": <inspect registered --json>,
     "active_queues": <inspect active_queues --json>}
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from app.schemas._base import FrozenStrictModel


# docker-compose.yml declares one ``--queues=`` set per worker service. This is
# that same topology as settings field names; tests/test_sync_queue_check.py
# pins it against the compose commands so the two cannot drift apart.
WORKER_QUEUE_SETTING_NAMES: Mapping[str, tuple[str, ...]] = {
    "worker": ("CELERY_OPS_QUEUE", "CELERY_TASK_DEFAULT_QUEUE"),
    "inference-worker": ("CELERY_ML_INFERENCE_QUEUE",),
    "ml-worker": ("CELERY_ML_BACKFILL_QUEUE", "CELERY_ML_REEVALUATION_QUEUE"),
    "training-worker": ("CELERY_ML_TRAINING_QUEUE",),
}

QUEUE_SETTING_NAMES: tuple[str, ...] = tuple(
    dict.fromkeys(
        setting_name
        for setting_names in WORKER_QUEUE_SETTING_NAMES.values()
        for setting_name in setting_names
    )
)

DEFAULT_QUEUE_SETTING_NAME = "CELERY_TASK_DEFAULT_QUEUE"

MESSAGE_PREFIX = "[queue-gate]"


class QueueBinding(BaseModel):
    """One ``active_queues`` entry; celery's exchange/routing keys are ignored."""

    name: str


class InspectPayload(FrozenStrictModel):
    """The combined ``celery inspect`` reply the shell gate pipes in."""

    registered: dict[str, list[str]] = Field(default_factory=dict)
    active_queues: dict[str, list[QueueBinding]] = Field(default_factory=dict)


class BeatEntry(BaseModel):
    """A beat schedule entry; only the dispatched task name is gated."""

    task: str


class TaskRoute(BaseModel):
    """A ``task_routes`` entry; an absent queue means the default queue."""

    queue: str | None = None


class LiveWiring(FrozenStrictModel):
    """Expected topology read from the same sources beat and compose use."""

    queue_names: dict[str, str]
    routed_queues: dict[str, str]


BEAT_SCHEDULE_ADAPTER = TypeAdapter(dict[str, BeatEntry])
TASK_ROUTES_ADAPTER = TypeAdapter(dict[str, TaskRoute])


def _format_queues(queues: Iterable[str]) -> str:
    return "{" + ", ".join(sorted(queues)) + "}"


def parse_active_queues(payload: InspectPayload) -> dict[str, frozenset[str]]:
    """Map each replying node to the queue names it actually consumes."""
    return {
        node: frozenset(binding.name for binding in bindings)
        for node, bindings in payload.active_queues.items()
    }


def _registered_task_name(entry: str) -> str:
    """Drop celery's task info suffix: ``jobs.x [rate_limit=10/m]`` → ``jobs.x``.

    ``inspect registered`` appends ``[exchange=… routing_key=… rate_limit=…]``
    for any task that sets one of them, so giving a gated task a rate limit
    would otherwise read as a missing registration and keep beat down.
    """
    return entry.split(" [", 1)[0]


def parse_registered_tasks(payload: InspectPayload) -> dict[str, frozenset[str]]:
    """Map each replying node to the task names registered on it."""
    return {
        node: frozenset(_registered_task_name(entry) for entry in tasks)
        for node, tasks in payload.registered.items()
    }


def resolve_expected_queue_sets(
    queue_names_by_setting: Mapping[str, str],
) -> dict[str, frozenset[str]]:
    """Resolve the declared topology into concrete queue names per service."""
    return {
        service: frozenset(
            queue_names_by_setting[setting_name] for setting_name in setting_names
        )
        for service, setting_names in WORKER_QUEUE_SETTING_NAMES.items()
    }


def check_queue_settings(queue_names_by_setting: Mapping[str, str]) -> list[str]:
    """Distinct queue names are what makes per-worker isolation observable."""
    problems: list[str] = []
    setting_by_value: dict[str, str] = {}
    for setting_name in QUEUE_SETTING_NAMES:
        value = queue_names_by_setting[setting_name]
        previous = setting_by_value.get(value)
        if previous is not None:
            problems.append(
                f"{MESSAGE_PREFIX} settings {previous} and {setting_name} both resolve to "
                f"queue '{value}'; per-worker isolation is unverifiable"
            )
        setting_by_value[value] = setting_name
    return problems


def _group_nodes_by_service(
    queues_by_node: Mapping[str, frozenset[str]],
    expected_by_service: Mapping[str, frozenset[str]],
) -> tuple[dict[str, list[str]], list[tuple[str, frozenset[str]]]]:
    service_by_queue_set = {
        queues: service for service, queues in expected_by_service.items()
    }
    matched: dict[str, list[str]] = {service: [] for service in expected_by_service}
    unexpected: list[tuple[str, frozenset[str]]] = []
    for node, queues in sorted(queues_by_node.items()):
        service = service_by_queue_set.get(queues)
        if service is None:
            unexpected.append((node, queues))
        else:
            matched[service].append(node)
    return matched, unexpected


def check_queue_isolation(
    queues_by_node: Mapping[str, frozenset[str]],
    expected_by_service: Mapping[str, frozenset[str]],
) -> list[str]:
    """Require an exact one-node-per-declared-queue-set match, both directions."""
    matched, unexpected = _group_nodes_by_service(queues_by_node, expected_by_service)
    consumed: frozenset[str] = (
        frozenset().union(*queues_by_node.values()) if queues_by_node else frozenset()
    )

    uncovered: list[str] = []
    mismatched: list[str] = []
    for service in sorted(expected_by_service):
        expected = expected_by_service[service]
        for queue in sorted(expected - consumed):
            uncovered.append(
                f"{MESSAGE_PREFIX} queue '{queue}' ({service}) has no active consumer"
            )
        nodes = matched[service]
        if len(nodes) != 1:
            found = f" (found: {', '.join(nodes)})" if nodes else ""
            mismatched.append(
                f"{MESSAGE_PREFIX} service '{service}' expects exactly one node consuming "
                f"{_format_queues(expected)}; matched {len(nodes)}{found}"
            )

    drifted = [
        f"{MESSAGE_PREFIX} node '{node}' consumes {_format_queues(queues)}, which matches no "
        "declared worker queue set (per-worker isolation drift)"
        for node, queues in unexpected
    ]
    return uncovered + mismatched + drifted


def scheduled_task_queues(
    schedule: Mapping[str, BeatEntry],
    routes: Mapping[str, TaskRoute],
    default_queue: str,
) -> dict[str, str]:
    """Resolve every beat-scheduled task to the queue it is dispatched on."""
    queue_by_task: dict[str, str] = {}
    for entry in schedule.values():
        route = routes.get(entry.task)
        queue = route.queue if route is not None else None
        queue_by_task[entry.task] = queue or default_queue
    return queue_by_task


def check_scheduled_task_registration(
    registered_by_node: Mapping[str, frozenset[str]],
    queues_by_node: Mapping[str, frozenset[str]],
    queue_by_task: Mapping[str, str],
) -> list[str]:
    """Every consumer of a scheduled task's queue must have that task registered."""
    problems: list[str] = []
    for task in sorted(queue_by_task):
        queue = queue_by_task[task]
        consumers = sorted(
            node for node, queues in queues_by_node.items() if queue in queues
        )
        if not consumers:
            problems.append(
                f"{MESSAGE_PREFIX} scheduled task '{task}' routes to queue '{queue}' "
                "with no active consumer"
            )
            continue
        missing = [
            node
            for node in consumers
            if task not in registered_by_node.get(node, frozenset())
        ]
        if missing:
            problems.append(
                f"{MESSAGE_PREFIX} scheduled task '{task}' is not registered on "
                f"{', '.join(missing)} consuming its routed queue '{queue}'"
            )
    return problems


def run_checks(payload: InspectPayload, wiring: LiveWiring) -> list[str]:
    """Return every topology drift found; an empty list means the gate passes."""
    problems = check_queue_settings(wiring.queue_names)

    queues_by_node = parse_active_queues(payload)
    if not queues_by_node:
        return problems + [f"{MESSAGE_PREFIX} no worker replied to inspect active_queues"]

    problems += check_queue_isolation(
        queues_by_node, resolve_expected_queue_sets(wiring.queue_names)
    )
    problems += check_scheduled_task_registration(
        parse_registered_tasks(payload), queues_by_node, wiring.routed_queues
    )
    return problems


def load_live_wiring() -> LiveWiring:
    """Read the expected topology from the same settings/beat sources beat uses."""
    from app.core.config import settings
    from app.tasks.celery_app import build_celery_runtime_config, build_task_routes

    queue_names = {
        setting_name: str(getattr(settings, setting_name))
        for setting_name in QUEUE_SETTING_NAMES
    }
    schedule = BEAT_SCHEDULE_ADAPTER.validate_python(
        build_celery_runtime_config()["beat_schedule"]
    )
    routes = TASK_ROUTES_ADAPTER.validate_python(build_task_routes())
    return LiveWiring(
        queue_names=queue_names,
        routed_queues=scheduled_task_queues(
            schedule, routes, queue_names[DEFAULT_QUEUE_SETTING_NAME]
        ),
    )


def main() -> int:
    try:
        payload = InspectPayload.model_validate_json(sys.stdin.read())
    except ValidationError as exc:
        print(
            f"{MESSAGE_PREFIX} could not read celery inspect payload: {exc}",
            file=sys.stderr,
        )
        return 1

    wiring = load_live_wiring()
    problems = run_checks(payload, wiring)
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        return 1

    print(
        f"{MESSAGE_PREFIX} ok: {len(payload.active_queues)} nodes match the declared "
        f"queue topology ({len(wiring.queue_names)} queues), "
        f"{len(wiring.routed_queues)} scheduled tasks registered"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

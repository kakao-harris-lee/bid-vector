"""Behavior tests for the sync-after-merge queue topology gate.

The gate this covers replaced a union check that could not see per-worker
isolation drift: as long as every configured queue appeared on *some* node, the
old check passed even if the ops worker was also draining the ML inference
queue. These fixtures pin the drift shapes that must fail the gate.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from scripts._sync_queue_check import (
    DEFAULT_QUEUE_SETTING_NAME,
    QUEUE_SETTING_NAMES,
    WORKER_QUEUE_SETTING_NAMES,
    BeatEntry,
    InspectPayload,
    LiveWiring,
    TaskRoute,
    check_queue_settings,
    load_live_wiring,
    parse_active_queues,
    parse_registered_tasks,
    resolve_expected_queue_sets,
    run_checks,
    scheduled_task_queues,
)

COMPOSE_PATH = Path(__file__).resolve().parents[1] / "docker-compose.yml"

QUEUE_NAMES = {
    "CELERY_TASK_DEFAULT_QUEUE": "bid_vector",
    "CELERY_OPS_QUEUE": "bid_vector_ops",
    "CELERY_ML_INFERENCE_QUEUE": "bid_vector_ml_inference",
    "CELERY_ML_BACKFILL_QUEUE": "bid_vector_ml_backfill",
    "CELERY_ML_REEVALUATION_QUEUE": "bid_vector_ml_reevaluation",
    "CELERY_ML_TRAINING_QUEUE": "bid_vector_ml_training",
}

NODE_BY_SERVICE = {
    "worker": "celery@ops",
    "inference-worker": "celery@inference",
    "ml-worker": "celery@ml",
    "training-worker": "celery@training",
}

QUEUE_BY_TASK = {
    "jobs.collect_koneps_notices": QUEUE_NAMES["CELERY_OPS_QUEUE"],
    "jobs.monitor_operator_strategy": QUEUE_NAMES["CELERY_ML_INFERENCE_QUEUE"],
    "ml.train_price_predictor": QUEUE_NAMES["CELERY_ML_TRAINING_QUEUE"],
}

WIRING = LiveWiring(queue_names=QUEUE_NAMES, routed_queues=QUEUE_BY_TASK)


def _queues_by_node(overrides: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
    expected = resolve_expected_queue_sets(QUEUE_NAMES)
    topology = {
        NODE_BY_SERVICE[service]: sorted(queues) for service, queues in expected.items()
    }
    topology.update(overrides or {})
    return topology


def _build_payload(
    *,
    queue_overrides: dict[str, list[str]] | None = None,
    drop_nodes: tuple[str, ...] = (),
    registered_overrides: dict[str, list[str]] | None = None,
) -> InspectPayload:
    queues_by_node = _queues_by_node(queue_overrides)
    for node in drop_nodes:
        queues_by_node.pop(node, None)

    registered = {node: sorted(QUEUE_BY_TASK) for node in queues_by_node}
    registered.update(registered_overrides or {})
    return InspectPayload.model_validate(
        {
            "active_queues": {
                node: [{"name": queue} for queue in queues]
                for node, queues in queues_by_node.items()
            },
            "registered": registered,
        }
    )


def _run(payload: InspectPayload) -> list[str]:
    return run_checks(payload, WIRING)


class TestHealthyTopology:
    def test_declared_topology_passes(self):
        assert _run(_build_payload()) == []

    def test_every_configured_queue_including_default_is_covered(self):
        expected = resolve_expected_queue_sets(QUEUE_NAMES)
        covered = frozenset().union(*expected.values())

        assert covered == frozenset(QUEUE_NAMES.values())
        assert QUEUE_NAMES[DEFAULT_QUEUE_SETTING_NAME] in expected["worker"]


class TestQueueIsolationDrift:
    def test_ops_worker_also_consuming_ml_queue_fails(self):
        """The regression the old union check was blind to."""
        payload = _build_payload(
            queue_overrides={
                "celery@ops": [
                    QUEUE_NAMES["CELERY_OPS_QUEUE"],
                    QUEUE_NAMES["CELERY_TASK_DEFAULT_QUEUE"],
                    QUEUE_NAMES["CELERY_ML_INFERENCE_QUEUE"],
                ]
            }
        )
        problems = _run(payload)

        assert any("isolation drift" in problem for problem in problems)
        assert any("celery@ops" in problem for problem in problems)
        assert any("service 'worker'" in problem for problem in problems)

    def test_union_of_all_queues_alone_does_not_pass_the_gate(self):
        payload = _build_payload(
            queue_overrides={"celery@ops": sorted(QUEUE_NAMES.values())},
            drop_nodes=("celery@inference", "celery@ml", "celery@training"),
        )

        assert _run(payload), "a single node draining every queue must fail the gate"

    def test_extra_undeclared_queue_on_declared_node_fails(self):
        payload = _build_payload(
            queue_overrides={
                "celery@training": [
                    QUEUE_NAMES["CELERY_ML_TRAINING_QUEUE"],
                    "bid_vector_experimental",
                ]
            }
        )
        problems = _run(payload)

        assert any("bid_vector_experimental" in problem for problem in problems)
        assert any("service 'training-worker'" in problem for problem in problems)

    def test_duplicate_node_for_one_service_fails(self):
        payload = _build_payload(
            queue_overrides={
                "celery@inference-2": [QUEUE_NAMES["CELERY_ML_INFERENCE_QUEUE"]]
            }
        )
        problems = _run(payload)

        assert any(
            "service 'inference-worker'" in problem and "matched 2" in problem
            for problem in problems
        )


class TestMissingConsumers:
    def test_missing_default_queue_consumer_is_reported_by_name(self):
        payload = _build_payload(
            queue_overrides={"celery@ops": [QUEUE_NAMES["CELERY_OPS_QUEUE"]]}
        )
        problems = _run(payload)

        assert any(
            f"queue '{QUEUE_NAMES[DEFAULT_QUEUE_SETTING_NAME]}' (worker) has no active consumer"
            in problem
            for problem in problems
        )

    def test_missing_node_reports_its_queues_and_service(self):
        payload = _build_payload(drop_nodes=("celery@ml",))
        problems = _run(payload)

        assert any(
            f"queue '{QUEUE_NAMES['CELERY_ML_BACKFILL_QUEUE']}' (ml-worker)" in problem
            for problem in problems
        )
        assert any(
            "service 'ml-worker'" in problem and "matched 0" in problem
            for problem in problems
        )

    def test_no_replying_worker_fails_closed(self):
        problems = run_checks(InspectPayload(), WIRING)

        assert any("no worker replied" in problem for problem in problems)


class TestScheduledTaskRegistration:
    def test_task_missing_on_its_routed_queue_consumer_fails(self):
        payload = _build_payload(
            registered_overrides={
                "celery@training": [
                    "jobs.collect_koneps_notices",
                    "jobs.monitor_operator_strategy",
                ]
            }
        )
        problems = _run(payload)

        assert any(
            "ml.train_price_predictor" in problem and "celery@training" in problem
            for problem in problems
        )

    def test_registration_is_scoped_to_the_routed_queue_consumers(self):
        """An ML-only task absent from the ops worker is not a failure."""
        payload = _build_payload(
            registered_overrides={
                "celery@ops": [
                    "jobs.collect_koneps_notices",
                    "jobs.monitor_operator_strategy",
                ]
            }
        )

        assert _run(payload) == []

    def test_task_routed_to_an_unconsumed_queue_fails(self):
        wiring = LiveWiring(
            queue_names=QUEUE_NAMES,
            routed_queues={"jobs.orphan": "bid_vector_retired"},
        )
        problems = run_checks(_build_payload(), wiring)

        assert any(
            "jobs.orphan" in problem and "no active consumer" in problem
            for problem in problems
        )


class TestScheduleResolution:
    def test_unrouted_task_falls_back_to_the_default_queue(self):
        queue_by_task = scheduled_task_queues(
            {"unrouted": BeatEntry(task="jobs.unrouted")},
            {},
            QUEUE_NAMES[DEFAULT_QUEUE_SETTING_NAME],
        )

        assert queue_by_task == {"jobs.unrouted": QUEUE_NAMES[DEFAULT_QUEUE_SETTING_NAME]}

    def test_route_without_a_queue_falls_back_to_the_default_queue(self):
        queue_by_task = scheduled_task_queues(
            {"entry": BeatEntry(task="jobs.x")},
            {"jobs.x": TaskRoute()},
            "bid_vector",
        )

        assert queue_by_task == {"jobs.x": "bid_vector"}

    def test_routed_task_uses_its_declared_queue(self):
        queue_by_task = scheduled_task_queues(
            {"entry": BeatEntry(task="jobs.x")},
            {"jobs.x": TaskRoute(queue="bid_vector_ops")},
            "bid_vector",
        )

        assert queue_by_task == {"jobs.x": "bid_vector_ops"}


class TestPayloadContract:
    def test_celery_queue_metadata_beyond_the_name_is_accepted(self):
        payload = InspectPayload.model_validate(
            {
                "active_queues": {
                    "celery@a": [
                        {"name": "q", "exchange": {"name": "q"}, "routing_key": "q"}
                    ]
                },
                "registered": {"celery@a": ["jobs.x"]},
            }
        )

        assert parse_active_queues(payload) == {"celery@a": frozenset({"q"})}
        assert parse_registered_tasks(payload) == {"celery@a": frozenset({"jobs.x"})}

    def test_missing_sections_default_to_empty(self):
        assert InspectPayload().active_queues == {}
        assert InspectPayload().registered == {}

    def test_unknown_top_level_section_is_rejected(self):
        with pytest.raises(ValidationError):
            InspectPayload.model_validate({"registerd": {}})

    def test_queue_entry_without_a_name_is_rejected(self):
        with pytest.raises(ValidationError):
            InspectPayload.model_validate({"active_queues": {"celery@a": [{}]}})


class TestQueueSettings:
    def test_distinct_queue_names_pass(self):
        assert check_queue_settings(QUEUE_NAMES) == []

    def test_collapsed_queue_names_are_rejected(self):
        collapsed = dict(QUEUE_NAMES, CELERY_ML_INFERENCE_QUEUE="bid_vector_ops")
        problems = check_queue_settings(collapsed)

        assert any("isolation is unverifiable" in problem for problem in problems)

    def test_every_queue_setting_is_claimed_by_exactly_one_service(self):
        claimed = [name for names in WORKER_QUEUE_SETTING_NAMES.values() for name in names]

        assert sorted(claimed) == sorted(QUEUE_SETTING_NAMES)
        assert len(claimed) == len(set(claimed))


def _compose_queue_setting_names(service_name: str) -> set[str]:
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    command = str(compose["services"][service_name]["command"])
    match = re.search(r"--queues=(\S+)", command)
    assert match, f"{service_name} declares no --queues"
    return set(re.findall(r"\$\{([A-Z_]+)(?::-[^}]*)?\}", match.group(1)))


@pytest.mark.parametrize("service_name", sorted(WORKER_QUEUE_SETTING_NAMES))
def test_declared_topology_matches_compose_queues(service_name: str):
    """The gate's expectation must be the compose declaration, not a copy of it."""
    assert _compose_queue_setting_names(service_name) == set(
        WORKER_QUEUE_SETTING_NAMES[service_name]
    )


def test_compose_declares_no_worker_outside_the_declared_topology():
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    celery_workers = {
        name
        for name, service in compose["services"].items()
        if "--queues=" in str(service.get("command", ""))
    }

    assert celery_workers == set(WORKER_QUEUE_SETTING_NAMES)


class TestLiveWiring:
    def test_gate_reads_queue_names_and_schedule_from_the_running_config(self):
        wiring = load_live_wiring()

        assert set(wiring.queue_names) == set(QUEUE_SETTING_NAMES)
        assert all(wiring.queue_names.values())
        # The gate verifies whatever beat will actually dispatch here: the
        # outbox schedules default on, so they must appear.
        assert (
            wiring.routed_queues["jobs.process_inference_outbox"]
            == wiring.queue_names["CELERY_ML_INFERENCE_QUEUE"]
        )
        assert (
            wiring.routed_queues["jobs.stage_active_similarity_projection_backfill"]
            == wiring.queue_names["CELERY_ML_INFERENCE_QUEUE"]
        )

    def test_schedules_disabled_in_this_environment_are_not_gated(self):
        from app.core.config import settings

        wiring = load_live_wiring()

        assert not settings.NOTIFICATION_DELIVERY_OUTBOX_SCHEDULE_ENABLED
        assert "jobs.process_notification_delivery_outbox" not in wiring.routed_queues

    def test_formerly_hardcoded_gate_tasks_resolve_to_their_routed_queues(self):
        """The shell no longer lists task names; routing must still be exact."""
        from app.tasks.celery_app import build_task_routes

        from scripts._sync_queue_check import TASK_ROUTES_ADAPTER

        wiring = load_live_wiring()
        ops_queue = wiring.queue_names["CELERY_OPS_QUEUE"]
        inference_queue = wiring.queue_names["CELERY_ML_INFERENCE_QUEUE"]
        expected = {
            "jobs.collect_koneps_notices": ops_queue,
            "jobs.monitor_operator_strategy": inference_queue,
            "jobs.process_inference_outbox": inference_queue,
            "jobs.process_notification_delivery_outbox": ops_queue,
            "jobs.reconcile_stale_task_runs": ops_queue,
            "jobs.stage_active_similarity_projection_backfill": inference_queue,
        }
        schedule = {name: BeatEntry(task=name) for name in expected}

        assert (
            scheduled_task_queues(
                schedule,
                TASK_ROUTES_ADAPTER.validate_python(build_task_routes()),
                wiring.queue_names[DEFAULT_QUEUE_SETTING_NAME],
            )
            == expected
        )

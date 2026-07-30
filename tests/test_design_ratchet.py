"""설계 래칫(scripts/design_ratchet.py) 계약 + 저장소 통합 테스트.

래칫의 계약은 단 하나다: **규율 위반 카운트는 파일·지표 단위로 늘어나지 않는다**
(줄어들거나 사라지는 것은 항상 통과). 여기서는
  1. 순수 비교 함수 ``compare_reports`` 의 증가/감소/신규파일 판정,
  2. baseline JSON 계약(``extra="forbid"`` · 타입 강제),
  3. AST 스캐너의 지표별 인식,
  4. 실제 저장소 스캔이 baseline 을 초과하지 않음
을 고정한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts import design_ratchet

from scripts._design_ratchet_scan import (
    REPO_ROOT,
    FileMetrics,
    RatchetReport,
    RatchetScanError,
    compare_reports,
    count_improvements,
    file_loc_band,
    scan_repo,
    scan_source,
)
from scripts.design_ratchet import (
    BASELINE_PATH,
    format_baseline_delta,
    format_totals,
    format_violations,
    load_baseline,
    run_baseline_update,
    save_baseline,
)

RATCHET_FAILURE_HINT = (
    "설계 래칫 위반: 규율 위반 카운트가 baseline 보다 늘었습니다.\n"
    "위반을 없애는 것이 기본 대응입니다(함수 분해 · DTO 경계 승격 · json 직접 호출 제거).\n"
    "정당한 경우 `python scripts/design_ratchet.py --update-baseline` 후 사유를 PR 본문에 기재하세요."
)


def _report(**files: FileMetrics) -> RatchetReport:
    """테스트용 리포트 조립 헬퍼(키의 ``__`` 는 ``/`` 로 복원)."""
    return RatchetReport(
        files={name.replace("__", "/"): metrics for name, metrics in files.items()}
    )


class TestCompareReports:
    def test_identical_reports_have_no_violation(self) -> None:
        baseline = _report(app__a=FileMetrics(functions_over_soft_limit=2))
        current = _report(app__a=FileMetrics(functions_over_soft_limit=2))
        assert compare_reports(baseline, current) == []

    def test_increase_is_violation(self) -> None:
        baseline = _report(app__a=FileMetrics(functions_over_soft_limit=2))
        current = _report(app__a=FileMetrics(functions_over_soft_limit=3))
        violations = compare_reports(baseline, current)
        assert len(violations) == 1
        assert violations[0].metric == "functions_over_soft_limit"
        assert violations[0].file == "app/a"
        assert violations[0].baseline == 2
        assert violations[0].current == 3

    def test_decrease_is_allowed(self) -> None:
        baseline = _report(app__a=FileMetrics(json_direct_calls=5))
        current = _report(app__a=FileMetrics(json_direct_calls=1))
        assert compare_reports(baseline, current) == []

    def test_removed_file_is_allowed(self) -> None:
        baseline = _report(app__a=FileMetrics(json_direct_calls=5))
        assert compare_reports(baseline, RatchetReport()) == []

    def test_new_file_with_nonzero_count_is_violation(self) -> None:
        current = _report(app__new=FileMetrics(dict_boundary_functions=1))
        violations = compare_reports(RatchetReport(), current)
        assert len(violations) == 1
        assert violations[0].metric == "dict_boundary_functions"
        assert violations[0].baseline == 0
        assert violations[0].current == 1

    def test_new_clean_file_is_allowed(self) -> None:
        current = _report(app__new=FileMetrics())
        assert compare_reports(RatchetReport(), current) == []

    def test_reports_every_regressed_metric(self) -> None:
        baseline = _report(app__a=FileMetrics(json_direct_calls=1, file_loc_band=24))
        current = _report(app__a=FileMetrics(json_direct_calls=2, file_loc_band=28))
        metrics = {violation.metric for violation in compare_reports(baseline, current)}
        assert metrics == {"json_direct_calls", "file_loc_band"}

    def test_improvement_in_one_file_does_not_mask_regression_in_another(self) -> None:
        baseline = _report(
            app__a=FileMetrics(json_direct_calls=5),
            app__b=FileMetrics(json_direct_calls=1),
        )
        current = _report(
            app__a=FileMetrics(json_direct_calls=0),
            app__b=FileMetrics(json_direct_calls=2),
        )
        violations = compare_reports(baseline, current)
        assert [violation.file for violation in violations] == ["app/b"]

    def test_crossing_the_file_loc_limit_is_violation(self) -> None:
        """500 이하 파일은 baseline 항목이 없으므로 초과 진입 자체가 위반이다."""
        violations = compare_reports(
            RatchetReport(), _report(app__a=FileMetrics(file_loc_band=21))
        )
        assert [violation.metric for violation in violations] == ["file_loc_band"]


class TestCountImprovements:
    def test_counts_reductions_per_metric(self) -> None:
        baseline = _report(app__a=FileMetrics(json_direct_calls=5, env_test_sniff=2))
        current = _report(app__a=FileMetrics(json_direct_calls=1, env_test_sniff=2))
        reduced = count_improvements(baseline, current)
        assert reduced.json_direct_calls == 4
        assert reduced.env_test_sniff == 0

    def test_counts_deleted_file_as_reduction(self) -> None:
        baseline = _report(app__gone=FileMetrics(dict_boundary_functions=3))
        reduced = count_improvements(baseline, RatchetReport())
        assert reduced.dict_boundary_functions == 3

    def test_increase_is_not_counted_as_reduction(self) -> None:
        baseline = _report(app__a=FileMetrics(json_direct_calls=1))
        current = _report(app__a=FileMetrics(json_direct_calls=4))
        assert count_improvements(baseline, current).json_direct_calls == 0


class TestBaselineDelta:
    def test_delta_reports_regressions_new_and_removed_files(self) -> None:
        baseline = _report(
            app__gone=FileMetrics(json_direct_calls=2),
            app__kept=FileMetrics(json_direct_calls=3),
        )
        current = _report(
            app__kept=FileMetrics(json_direct_calls=5),
            app__fresh=FileMetrics(env_test_sniff=1),
        )
        rendered = format_baseline_delta(baseline, current)
        assert "증가(위반) 2건" in rendered
        assert "json_direct_calls: app/kept 3 -> 5" in rendered
        assert "신규 등장 파일 1개: app/fresh" in rendered
        assert "사라진 파일 1개: app/gone" in rendered
        assert "감소 총량: json_direct_calls -2" in rendered

    def test_delta_reports_no_change(self) -> None:
        report = _report(app__a=FileMetrics(json_direct_calls=1))
        rendered = format_baseline_delta(report, report)
        assert "증가(위반) 0건" in rendered
        assert "신규 등장 파일 0개" in rendered
        assert "감소 총량: 없음" in rendered


class TestBaselineUpdate:
    """``--update-baseline`` 은 덮어쓰기 전에 무엇이 바뀌는지 보여줘야 한다."""

    def test_prints_delta_before_overwriting(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        path = tmp_path / "baseline.json"
        save_baseline(_report(app__a=FileMetrics(json_direct_calls=5)), path)
        monkeypatch.setattr(design_ratchet, "BASELINE_PATH", path)
        current = _report(app__a=FileMetrics(json_direct_calls=1))

        assert run_baseline_update(current) == 0

        printed = capsys.readouterr().out
        assert "감소 총량: json_direct_calls -4" in printed
        assert load_baseline(path) == current

    def test_incompatible_baseline_skips_delta_but_still_saves(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """지표 개명 후 구 baseline 은 해석 불가 — 재생성은 막히지 않아야 한다."""
        path = tmp_path / "baseline.json"
        path.write_text('{"files": {"app/a": {"legacy_metric": 3}}}', encoding="utf-8")
        monkeypatch.setattr(design_ratchet, "BASELINE_PATH", path)
        current = _report(app__a=FileMetrics(json_direct_calls=1))

        assert run_baseline_update(current) == 0

        printed = capsys.readouterr().out
        assert design_ratchet.INCOMPATIBLE_BASELINE_NOTE in printed
        assert "extra_forbidden" in printed  # 원본 에러 요약 1줄
        assert "baseline delta" not in printed
        assert load_baseline(path) == current

    @pytest.mark.parametrize(
        "corrupt",
        ["{not json", "", '{"files": {"app/a": {"json_direct_calls": 1}}'],
        ids=["garbage", "empty", "truncated"],
    )
    def test_corrupt_baseline_is_not_swallowed(
        self,
        corrupt: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """손상·머지 컨플릭트 잔여물은 "지표 드리프트"가 아니므로 삼키지 않는다."""
        path = tmp_path / "baseline.json"
        path.write_text(corrupt, encoding="utf-8")
        monkeypatch.setattr(design_ratchet, "BASELINE_PATH", path)
        original = path.read_text(encoding="utf-8")

        with pytest.raises(ValidationError):
            run_baseline_update(_report(app__a=FileMetrics(json_direct_calls=1)))

        # 덮어쓰기 전에 멈춘다 — 운영자가 파일을 먼저 확인해야 한다.
        assert path.read_text(encoding="utf-8") == original

    def test_root_shape_mismatch_is_not_swallowed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "baseline.json"
        path.write_text("[]", encoding="utf-8")
        monkeypatch.setattr(design_ratchet, "BASELINE_PATH", path)

        with pytest.raises(ValidationError):
            run_baseline_update(_report(app__a=FileMetrics(json_direct_calls=1)))

    def test_missing_baseline_is_created_without_delta(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        path = tmp_path / "baseline.json"
        monkeypatch.setattr(design_ratchet, "BASELINE_PATH", path)
        current = _report(app__a=FileMetrics(json_direct_calls=1))

        assert run_baseline_update(current) == 0

        assert "baseline delta" not in capsys.readouterr().out
        assert load_baseline(path) == current


class TestTotalsFormatting:
    def test_file_loc_band_is_rendered_as_file_count_and_max_loc(self) -> None:
        report = _report(
            app__a=FileMetrics(file_loc_band=21),
            app__b=FileMetrics(file_loc_band=40),
        )
        rendered = format_totals(report)
        assert "file_loc_band: 500줄 초과 파일 2개(최대 ~1000줄)" in rendered
        assert "file_loc_band: 61" not in rendered

    def test_plain_metrics_are_rendered_as_sums(self) -> None:
        report = _report(app__a=FileMetrics(json_direct_calls=2))
        assert "json_direct_calls: 2" in format_totals(report)

    def test_absent_file_loc_band_is_reported_as_none(self) -> None:
        rendered = format_totals(_report(app__a=FileMetrics(json_direct_calls=1)))
        assert "file_loc_band: 500줄 초과 파일 없음" in rendered

    def test_file_loc_band_violation_explains_the_unit(self) -> None:
        """밴드 숫자만 보면 단위를 알 수 없으므로 환산 문구를 붙인다."""
        violations = compare_reports(
            _report(app__a=FileMetrics(file_loc_band=21)),
            _report(app__a=FileMetrics(file_loc_band=22)),
        )
        rendered = format_violations(violations)
        assert "file_loc_band: app/a 21 -> 22" in rendered
        assert "밴드≈25줄" in rendered
        assert "500줄 초과 진입 시 21부터" in rendered

    def test_other_metric_violations_have_no_suffix(self) -> None:
        violations = compare_reports(
            _report(app__a=FileMetrics(json_direct_calls=1)),
            _report(app__a=FileMetrics(json_direct_calls=2)),
        )
        assert violations[0].describe() == "  json_direct_calls: app/a 1 -> 2"


class TestReportSchema:
    def test_roundtrip(self) -> None:
        report = _report(app__a=FileMetrics(env_test_sniff=2))
        restored = RatchetReport.model_validate_json(report.model_dump_json())
        assert restored == report

    def test_unknown_metric_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RatchetReport.model_validate_json(
                '{"files": {"app/a": {"unknown_metric": 1}}}'
            )

    def test_unknown_top_level_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RatchetReport.model_validate_json('{"files": {}, "totals": {}}')

    def test_type_mismatch_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RatchetReport.model_validate_json(
                '{"files": {"app/a": {"json_direct_calls": "many"}}}'
            )

    def test_totals_are_summed_per_metric(self) -> None:
        report = _report(
            app__a=FileMetrics(json_direct_calls=2, env_test_sniff=1),
            app__b=FileMetrics(json_direct_calls=3),
        )
        totals = report.totals()
        assert totals.json_direct_calls == 5
        assert totals.env_test_sniff == 1


class TestScanSource:
    def test_counts_functions_over_soft_and_hard_limits(self) -> None:
        body = "\n".join(f"    x = {index}" for index in range(120))
        source = f"def big():\n{body}\n"
        metrics = scan_source("app/sample.py", source)
        assert metrics.functions_over_soft_limit == 1
        assert metrics.functions_over_hard_limit == 1

    def test_short_function_is_not_counted(self) -> None:
        metrics = scan_source("app/sample.py", "def small():\n    return 1\n")
        assert metrics.functions_over_soft_limit == 0
        assert metrics.functions_over_hard_limit == 0

    def test_file_loc_band_recorded_only_over_limit(self) -> None:
        small = scan_source("app/sample.py", "x = 1\n")
        assert small.file_loc_band == 0
        big = scan_source("app/sample.py", "x = 1\n" * 501)
        assert big.file_loc_band == 21

    def test_file_loc_band_absorbs_small_growth_but_not_large(self) -> None:
        """500줄 초과 파일에 한 줄 더해도 밴드는 그대로여야 한다(잡음 제거)."""
        assert file_loc_band("x\n" * 500) == 0
        assert file_loc_band("x\n" * 501) == 21
        assert file_loc_band("x\n" * 502) == 21
        assert file_loc_band("x\n" * 525) == 21
        assert file_loc_band("x\n" * 526) == 22

    def test_counts_json_direct_calls(self) -> None:
        source = (
            "import json\n\n\ndef f(raw):\n    return json.dumps(json.loads(raw))\n"
        )
        assert scan_source("app/sample.py", source).json_direct_calls == 2

    def test_allowlisted_file_json_calls_are_ignored(self) -> None:
        source = "import json\n\n\ndef f(raw):\n    return json.dumps(raw)\n"
        assert (
            scan_source("app/services/ml_release/signing.py", source).json_direct_calls
            == 0
        )

    def test_counts_dict_boundary_functions(self) -> None:
        source = (
            "from typing import Any\n\n\n"
            "def takes(payload: dict) -> None:\n    return None\n\n\n"
            "def returns(name: str) -> Any:\n    return name\n\n\n"
            "def typed(name: str) -> int:\n    return 1\n"
        )
        assert scan_source("app/sample.py", source).dict_boundary_functions == 2

    def test_dict_with_concrete_value_type_is_exempt(self) -> None:
        source = (
            "def report() -> dict[str, FileMetrics]:\n    return {}\n\n\n"
            "def mapping(rows: Mapping[str, int]) -> None:\n    return None\n"
        )
        assert scan_source("app/sample.py", source).dict_boundary_functions == 0

    def test_dict_with_scalar_value_type_is_exempt(self) -> None:
        """``dict[str, str]`` 은 타입이 명확한 계약이라 면제한다."""
        source = (
            "def texts() -> dict[str, str]:\n    return {}\n\n\n"
            "def counts(rows: dict[str, int]) -> None:\n    return None\n\n\n"
            "def rates(rows: dict[str, float]) -> None:\n    return None\n\n\n"
            "def registry() -> dict[str, BasePricePredictor]:\n    return {}\n"
        )
        assert scan_source("app/sample.py", source).dict_boundary_functions == 0

    def test_dict_with_object_value_type_is_counted(self) -> None:
        """``object`` 는 ``Any`` 보다 약한 계약이라 무비용 우회가 되면 안 된다."""
        source = (
            "def payload() -> dict[str, object]:\n    return {}\n\n\n"
            "def mapping(rows: Mapping[str, object]) -> None:\n    return None\n\n\n"
            "def bare(value: object) -> None:\n    return None\n"
        )
        assert scan_source("app/sample.py", source).dict_boundary_functions == 3

    def test_weak_payload_nested_in_containers_is_counted(self) -> None:
        """컨테이너로 한 겹 감싸 지표를 피할 수 없어야 한다."""
        source = (
            "def nested() -> dict[str, dict[str, object]]:\n    return {}\n\n\n"
            "def wrapped(rows: dict[str, list[dict[str, Any]]]) -> None:\n"
            "    return None\n\n\n"
            "def listed(rows: list[dict[str, Any]]) -> None:\n    return None\n"
        )
        assert scan_source("app/sample.py", source).dict_boundary_functions == 3

    def test_concrete_containers_are_exempt(self) -> None:
        source = (
            "def ids() -> list[int]:\n    return []\n\n\n"
            "def rows(items: list[FileMetrics]) -> None:\n    return None\n\n\n"
            "def pair(item: tuple[int, str]) -> None:\n    return None\n"
        )
        assert scan_source("app/sample.py", source).dict_boundary_functions == 0

    def test_dict_with_any_value_type_is_counted(self) -> None:
        source = (
            "def payload() -> dict[str, Any]:\n    return {}\n\n\n"
            "def bare() -> dict:\n    return {}\n\n\n"
            "def optional(raw: dict[str, Any] | None) -> None:\n    return None\n"
        )
        assert scan_source("app/sample.py", source).dict_boundary_functions == 3

    def test_star_args_annotations_are_counted(self) -> None:
        source = (
            "def kwargs_only(**kwargs: Any) -> None:\n    return None\n\n\n"
            "def args_only(*args: dict) -> None:\n    return None\n\n\n"
            "def strict(*args: int, **kwargs: str) -> None:\n    return None\n"
        )
        assert scan_source("app/sample.py", source).dict_boundary_functions == 2

    def test_counts_env_test_sniff_in_app_only(self) -> None:
        source = 'def f():\n    return settings.ENVIRONMENT == "test"\n'
        assert scan_source("app/sample.py", source).env_test_sniff == 1
        assert scan_source("scripts/sample.py", source).env_test_sniff == 0

    def test_env_sniff_ignores_other_environment_comparisons(self) -> None:
        source = 'def f():\n    return settings.ENVIRONMENT == "production"\n'
        assert scan_source("app/sample.py", source).env_test_sniff == 0

    def test_counts_unvalidated_dict_task(self) -> None:
        source = (
            "@celery_app.task(name='x')\n"
            "def run(payload: dict) -> None:\n"
            "    return payload\n"
        )
        assert scan_source("app/tasks/sample.py", source).unvalidated_dict_tasks == 1

    def test_task_with_model_validate_is_not_counted(self) -> None:
        source = (
            "@celery_app.task\n"
            "def run(payload: dict) -> None:\n"
            "    Payload.model_validate(payload)\n"
        )
        assert scan_source("app/tasks/sample.py", source).unvalidated_dict_tasks == 0

    def test_task_with_model_construction_is_not_counted(self) -> None:
        source = (
            "@shared_task\n"
            "def run(payload: dict) -> None:\n"
            "    Payload(**payload)\n"
        )
        assert scan_source("app/tasks/sample.py", source).unvalidated_dict_tasks == 0

    def test_typed_task_is_not_counted(self) -> None:
        source = (
            "@celery_app.task\ndef run(project_id: int) -> None:\n    return None\n"
        )
        assert scan_source("app/tasks/sample.py", source).unvalidated_dict_tasks == 0

    def test_non_task_dict_function_is_not_counted_as_task(self) -> None:
        source = "def run(payload: dict) -> None:\n    return None\n"
        assert scan_source("app/tasks/sample.py", source).unvalidated_dict_tasks == 0

    def test_task_with_kwargs_any_is_counted(self) -> None:
        """``**kwargs: Any`` 로 payload 를 받는 우회 경로도 잡는다."""
        source = "@celery_app.task\ndef run(**kwargs: Any) -> None:\n    return None\n"
        assert scan_source("app/tasks/sample.py", source).unvalidated_dict_tasks == 1

    def test_task_with_unannotated_argument_is_counted(self) -> None:
        """어노테이션을 지우는 것으로 지표를 피할 수 없어야 한다."""
        source = "@celery_app.task\ndef run(payload) -> None:\n    return None\n"
        assert scan_source("app/tasks/sample.py", source).unvalidated_dict_tasks == 1

    def test_bound_task_self_is_not_treated_as_payload(self) -> None:
        """celery ``bind=True`` 의 self 는 어노테이션이 없어도 payload 가 아니다."""
        source = (
            "@celery_app.task(bind=True)\n"
            "def run(self, project_id: int) -> None:\n"
            "    return None\n"
        )
        assert scan_source("app/tasks/sample.py", source).unvalidated_dict_tasks == 0

    def test_task_with_no_arguments_is_not_counted(self) -> None:
        source = "@celery_app.task\ndef run() -> None:\n    return None\n"
        assert scan_source("app/tasks/sample.py", source).unvalidated_dict_tasks == 0

    def test_task_with_list_of_dict_payload_is_counted(self) -> None:
        """``list[dict[str, Any]]`` payload 도 검증되지 않는 입력이다."""
        source = (
            "@celery_app.task(bind=True)\n"
            "def run(self, notices: list[dict[str, Any]]) -> None:\n"
            "    return None\n"
        )
        assert scan_source("app/tasks/sample.py", source).unvalidated_dict_tasks == 1

    def test_unparsable_source_raises(self) -> None:
        """파싱 실패를 침묵시키면 그 파일의 위반이 0 으로 사라진다 — 크게 실패한다."""
        with pytest.raises(RatchetScanError) as excinfo:
            scan_source("app/broken.py", "def broken(:\n")
        assert "app/broken.py" in str(excinfo.value)
        assert isinstance(excinfo.value.__cause__, SyntaxError)


class TestRepositoryRatchet:
    def test_baseline_file_exists_and_parses(self) -> None:
        assert BASELINE_PATH.exists(), (
            f"baseline 이 없습니다: {BASELINE_PATH}. "
            "`python scripts/design_ratchet.py --update-baseline` 으로 생성하세요."
        )
        # 빈 baseline(위반 0)도 정당한 상태다 — 존재·파싱만 검증한다.
        assert isinstance(load_baseline(BASELINE_PATH), RatchetReport)

    def test_current_repository_does_not_exceed_baseline(self) -> None:
        violations = compare_reports(load_baseline(BASELINE_PATH), scan_repo(REPO_ROOT))
        assert (
            not violations
        ), f"{RATCHET_FAILURE_HINT}\n\n{format_violations(violations)}"

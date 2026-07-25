"""money 타입 seam이 *실제로* 정적 회귀를 잡는지 증명하는 negative-typecheck 가드.

PR-9는 ``app/services/bid_base.py`` 경계 시그니처에 ``app.domain.money.BaseAmount``
(기초금액-basis)를 강제해, budget_estimate(추정가격 ex-VAT)나 예정가(YegaAmount)를
기초금액 자리에 넘기던 #162 류 혼동을 float 레벨이 아니라 **타입 레벨**에서 막는다.

NewType은 런타임 소거라 유닛 테스트로는 이 강제가 실제로 작동하는지 검증할 수 없다
(런타임엔 그냥 float). 그래서 이 가드는 mypy를 프로그램적으로 돌려 **오용 코드가
정말로 타입 에러를 낸다**는 것을 증명한다 — seam이 조용히 무력화되면(예: 파라미터
어노테이션이 float로 회귀) 이 테스트가 깨진다.

CI의 ``mypy app/``는 tests/ 를 검사하지 않으므로, 오용 예제는 임시 파일에 두고
저장소 mypy 설정으로 격리 검사한다. mypy 미설치 환경에서는 skip 한다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

mypy_api = pytest.importorskip("mypy.api")

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"

# 오용 예제. sink()는 BaseAmount(기초금액)만 받는다. 라인 순서가 아래 어써션의
# 기대 에러 라인과 맞물리므로 함부로 재배치하지 않는다.
_MISUSE_SNIPPET = """
from app.domain.money import BaseAmount, YegaAmount


def sink(base: BaseAmount) -> None:
    return None


def probe(budget_estimate: float, yega: YegaAmount) -> None:
    sink(budget_estimate)  # 추정가격(float) → 기초금액 자리: 반드시 에러
    sink(yega)             # 예정가(YegaAmount) → 기초금액 자리: 반드시 에러
    sink(BaseAmount(1.0))  # 올바른 basis: 에러 없어야 함
"""


def _run_mypy(snippet_path: Path) -> tuple[str, int]:
    """저장소 mypy 설정으로 스니펫만 검사. (stdout, exit_code) 반환.

    ``app`` 패키지 해석을 위해 cwd 를 저장소 루트로 고정한다(pytest 는 보통 루트에서
    돌지만 명시적으로 보장한다). mypy.api 는 cwd 기반으로 모듈을 찾는다.
    """
    prev_cwd = os.getcwd()
    os.chdir(_REPO_ROOT)
    try:
        stdout, _stderr, exit_code = mypy_api.run(
            [
                "--config-file",
                str(_PYPROJECT),
                "--no-incremental",
                "--no-error-summary",
                str(snippet_path),
            ]
        )
        return stdout, exit_code
    finally:
        os.chdir(prev_cwd)


def test_money_seam_rejects_wrong_basis_at_bid_base_param(tmp_path: Path) -> None:
    snippet = tmp_path / "money_seam_misuse.py"
    snippet.write_text(_MISUSE_SNIPPET, encoding="utf-8")

    stdout, exit_code = _run_mypy(snippet)

    # seam 이 살아 있으면 mypy 는 실패한다(두 오용 라인).
    assert exit_code == 1, f"mypy가 오용을 잡지 못함 — seam 무력화 의심:\n{stdout}"

    error_lines = [ln for ln in stdout.splitlines() if "error:" in ln]
    arg_type_errors = [ln for ln in error_lines if "[arg-type]" in ln]

    # 정확히 두 오용(추정가격 float · 예정가 YegaAmount)만 arg-type 에러여야 한다.
    assert (
        len(arg_type_errors) == 2
    ), f"기대: BaseAmount 자리 오용 2건, 실제 arg-type 에러 {len(arg_type_errors)}건:\n{stdout}"
    # 두 에러 모두 기대 대상이 BaseAmount 임을 확인(엉뚱한 에러로 통과 방지).
    assert all('expected "BaseAmount"' in ln for ln in arg_type_errors), stdout
    # 오용 소스 타입이 각각 float(추정가격)와 YegaAmount(예정가)인지 확인.
    joined = "\n".join(arg_type_errors)
    assert 'incompatible type "float"' in joined, stdout
    assert 'incompatible type "YegaAmount"' in joined, stdout


def test_money_seam_allows_correct_basis(tmp_path: Path) -> None:
    """올바른 basis(BaseAmount 생성자)는 통과 — seam 이 만능 거부가 아님을 증명."""
    snippet = tmp_path / "money_seam_valid.py"
    snippet.write_text(
        "from app.domain.money import BaseAmount\n\n\n"
        "def sink(base: BaseAmount) -> None:\n    return None\n\n\n"
        "def probe() -> None:\n    sink(BaseAmount(55_000_000.0))\n",
        encoding="utf-8",
    )

    stdout, exit_code = _run_mypy(snippet)

    assert exit_code == 0, f"올바른 basis 사용이 타입 에러를 냄:\n{stdout}"
    assert "error:" not in stdout, stdout

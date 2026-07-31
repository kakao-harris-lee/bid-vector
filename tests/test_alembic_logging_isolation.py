"""in-process alembic 실행이 전역 로깅을 오염시키지 않는다는 회귀 가드.

과거 함정: ``Config("alembic.ini")`` 로 마이그레이션을 돌리면 ``alembic/env.py`` 의
``fileConfig`` 가 기본값(``disable_existing_loggers=True``)으로 동작해 그 시점에 존재하던
모든 로거를 ``disabled`` 로 만들었다. 그래서 같은 pytest 프로세스에서 **뒤에** 실행되는
``caplog`` 기반 테스트가 빈 ``records`` 를 받았다(파일 단독 실행은 통과하고 전체 실행만
실패하는 순서 의존 실패). 실제로 ``tests/test_paper_bidding_run_payload.py`` 는 이 때문에
caplog 대신 로거에 직접 핸들러를 붙이는 방어를 하고 있다.

여기서 고정하는 두 겹의 계약:

1. 테스트는 파일 없는 Config(``tests.support.alembic_config.make_alembic_config``)로
   alembic 을 돌린다 → 로깅 설정 경로 자체가 발동하지 않는다.
2. ini 기반 Config 로 돌려도(운영 CLI 경로) ``env.py`` 가 기존 로거를 disable 하지
   않는다 → 어떤 in-process 호출자도 뒤따르는 caplog 를 깨지 않는다.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config

import app.core.config as app_config
from tests.support.alembic_config import ALEMBIC_INI_PATH, make_alembic_config

# 실제 caplog 기반 테스트(``tests/test_inprocess_scheduler.py``)가 의존하는 로거 —
# caplog 왕복 단정의 대상. disabled 회귀는 특정 로거가 아니라 **모든 기존 로거**를 훑는다.
_CAPLOG_SENTINEL_LOGGER = "app.services.inprocess_scheduler"
# alembic.ini 의 ``[logger_alembic]`` level(양성 단정용 — fileConfig 삭제 뮤테이션 봉쇄).
_INI_ALEMBIC_LOGGER = "alembic"
_INI_ALEMBIC_LEVEL = logging.INFO


def _existing_loggers() -> list[logging.Logger]:
    """현재 존재하는 로거(``PlaceHolder`` 제외) + root."""
    return [logging.getLogger()] + [
        logger
        for logger in list(logging.Logger.manager.loggerDict.values())
        if isinstance(logger, logging.Logger)
    ]


def _disabled_logger_names() -> set[str]:
    return {logger.name for logger in _existing_loggers() if logger.disabled}


@contextmanager
def _preserved_global_logging() -> Iterator[None]:
    """기존 모든 로거의 level·handlers·propagate·disabled 를 스냅샷 후 원복한다.

    ini 경로를 실제로 실행하는 테스트는 ``fileConfig`` 로 전역 로깅을 재설정한다. 가드가
    실패하는 순간(=회귀가 들어온 순간)에도 그 오염이 뒤따르는 테스트로 번지지 않게, 대상을
    고정 목록이 아니라 존재하는 로거 전체로 잡는다.
    """
    snapshot = [
        (logger, logger.level, logger.handlers[:], logger.propagate, logger.disabled)
        for logger in _existing_loggers()
    ]
    try:
        yield
    finally:
        for logger, level, handlers, propagate, disabled in snapshot:
            logger.setLevel(level)
            logger.handlers[:] = handlers
            logger.propagate = propagate
            logger.disabled = disabled


def _stamp_head(cfg: Config, monkeypatch, tmp_path: Path) -> None:
    """격리된 sqlite 에 대해 alembic env.py 를 실제로 로드/실행한다(stamp 로 최소 실행)."""
    url = f"sqlite:///{tmp_path / 'alembic_logging.db'}"
    monkeypatch.setattr(app_config.settings, "DATABASE_URL", url)
    command.stamp(cfg, "head")


def test_helper_config_does_not_load_logging_ini():
    """공용 빌더는 ini 를 읽지 않는다(=``fileConfig`` 가 호출될 수 없다)."""
    cfg = make_alembic_config()

    assert cfg.config_file_name is None
    assert Path(cfg.get_main_option("script_location")).is_dir()


def test_fileless_config_run_keeps_loggers_enabled(tmp_path, monkeypatch, caplog):
    """파일 없는 Config 로 alembic 을 돌린 뒤에도 로거와 caplog 가 그대로 동작한다."""
    sentinel = logging.getLogger(_CAPLOG_SENTINEL_LOGGER)  # 실행 전에 "기존 로거"로 존재시킨다
    # 사전 조건: 이 지점까지 아무도 이 로거를 disable 하지 않았다(순서 오염 canary).
    assert not sentinel.disabled
    disabled_before = _disabled_logger_names()

    _stamp_head(make_alembic_config(), monkeypatch, tmp_path)

    assert _disabled_logger_names() - disabled_before == set()

    with caplog.at_level(logging.INFO, logger=sentinel.name):
        sentinel.info("alembic-logging-isolation-sentinel")
    assert any(
        record.getMessage() == "alembic-logging-isolation-sentinel"
        for record in caplog.records
    ), "alembic 실행 후 caplog 가 레코드를 받지 못했다(전역 로깅이 오염됐다)"


def test_ini_config_run_does_not_disable_existing_loggers(tmp_path, monkeypatch):
    """ini 경로(운영 CLI 와 동일)로 돌려도 기존 로거가 disabled 되지 않는다.

    ``alembic/env.py`` 가 ``fileConfig(..., disable_existing_loggers=False)`` 를 쓰는지
    직접 검증한다. 기본값으로 되돌아가도, ``fileConfig`` 호출 자체를 지워도 실패한다
    (후자는 ini 의 ``[logger_alembic]`` level 양성 단정이 잡는다).

    트레이드오프: ``fileConfig`` 는 ``_clearExistingHandlers`` 로 기존 핸들러를 close 하므로
    이 테스트 이후 세션의 ``--log-file`` 충실도는 의도적으로 희생한다(핸들러 목록은 원복하되
    닫힌 스트림은 되살릴 수 없다). CI/기본 실행은 ``--log-file`` 을 쓰지 않아 무영향이다.
    """
    logging.getLogger(_CAPLOG_SENTINEL_LOGGER)  # 실행 전에 "기존 로거"로 존재시킨다
    disabled_before = _disabled_logger_names()

    with _preserved_global_logging():
        cfg = Config(str(ALEMBIC_INI_PATH))
        assert cfg.config_file_name is not None  # ini 로깅 경로를 실제로 태운다
        _stamp_head(cfg, monkeypatch, tmp_path)

        newly_disabled = _disabled_logger_names() - disabled_before
        # 양성 단정: ini 로깅이 실제로 적용됐다(=fileConfig 를 그냥 지운 게 아니다).
        ini_logger_level = logging.getLogger(_INI_ALEMBIC_LOGGER).level

    assert ini_logger_level == _INI_ALEMBIC_LEVEL, (
        "env.py stopped applying alembic.ini logging "
        f"({_INI_ALEMBIC_LOGGER} level={ini_logger_level})"
    )
    message = "alembic env.py 가 기존 로거를 disable 했다(이후 caplog 테스트가 조용히 깨진다)"
    assert newly_disabled == set(), f"{message}: {sorted(newly_disabled)}"

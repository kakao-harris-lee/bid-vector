"""in-process alembic 실행용 **로깅 안전** Config 빌더.

``Config("alembic.ini")`` 로 마이그레이션을 돌리면 ``alembic/env.py`` 가 ini 의 로깅
섹션을 ``logging.config.fileConfig`` 로 해석한다. 이 경로는 **전역 로깅을 재설정**한다
(root 핸들러 교체 + 기본값에서는 기존 로거 disable). pytest 프로세스에서는 그 부작용만
남아서, 마이그레이션을 돌린 테스트 뒤에 실행되는 ``caplog`` 기반 테스트가 빈 records 를
받는 순서 의존 실패가 생겼다.

마이그레이션 실행에 실제로 필요한 것은 ``script_location`` 하나뿐이다(``sqlalchemy.url``
은 ``alembic/env.py`` 가 ``settings.DATABASE_URL`` 로 덮는다). 그래서 테스트는 파일 없는
``Config`` 를 써서 로깅 설정 경로 자체를 발동시키지 않는다.

사용::

    from tests.support.alembic_config import make_alembic_config

    cfg = make_alembic_config()
    command.upgrade(cfg, "head")
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parents[2]
# 가드 테스트가 ini 경로를 의도적으로 태울 때만 쓴다(일반 테스트는 make_alembic_config()).
ALEMBIC_INI_PATH = REPO_ROOT / "alembic.ini"
ALEMBIC_SCRIPT_LOCATION = REPO_ROOT / "alembic"


def make_alembic_config() -> Config:
    """ini 파일을 읽지 않는 alembic ``Config`` (전역 로깅을 건드리지 않는다).

    경로 옵션은 cwd 와 무관하게 동작하도록 절대경로로 넣고, ini 의 ``prepend_sys_path = .``
    parity 를 유지해 마이그레이션/env.py 의 ``app`` import 가 cwd 에 의존하지 않게 한다.
    """
    config = Config()
    config.set_main_option("script_location", str(ALEMBIC_SCRIPT_LOCATION))
    config.set_main_option("prepend_sys_path", str(REPO_ROOT))
    return config

"""라우트가 ``Depends`` 로 주입받는 서비스 provider 단일 출처.

라우트 본문에서 서비스를 직접 생성하면(``service = XService()``) 테스트가 그 협력자를
갈아끼울 지점이 없어, 서비스 **내부**의 전역/모듈 속성을 monkeypatch 하는 넓은 우회가
필요해진다(예: KONEPS 획득 경로를 바꾸려고 ``http_client._default_http_get`` 을 교체).
provider 를 두면 테스트는 ``app.dependency_overrides`` 로 **협력자가 주입된 인스턴스**를
넘길 수 있다(§4.7-3 의존성 주입).

provider 는 라우터 파일마다 흩어지지 않게 여기 한 곳에 모은다(§4: 라우터는 얇게) — 라우터는
``Depends(get_...)`` 로 참조만 하고, 관례를 찾는 사람이 파일 하나만 보면 되도록 한다.

여기 있는 함수는 **생성만** 한다(도메인 로직 금지). 생성자가 부수효과 없이 협력자만
보관하는 서비스여야 요청당 새 인스턴스가 안전하다.
"""

from app.services.koneps.collector import KonepsCollectorService
from app.services.prediction_workflow import PredictionWorkflowService


def get_prediction_workflow() -> PredictionWorkflowService:
    """예측 워크플로 provider — ``app/api/predictions.py`` 의 두 라우트가 주입받는다.

    이 저장소의 provider 선례다(라우트가 서비스를 직접 만들지 않는다).
    ``tests/test_prediction_api_decoupling.py`` 가 이 키를 override 해 stub 워크플로로
    라우트를 구동한다 — 그 참조 경로(``app.api.predictions.get_prediction_workflow``)는
    라우터가 이 이름을 import 하므로 그대로 유지된다.
    """
    return PredictionWorkflowService()


def get_koneps_collector() -> KonepsCollectorService:
    """KONEPS 수집기 provider — 미주입이면 기본 ``requests`` 획득 경로를 쓴다.

    ``KonepsCollectorService`` 의 생성자는 HTTP 획득 seam 만 보관하는 부수효과 없는
    생성자라(연결/세션을 열지 않음) 요청마다 새로 만들어도 비용이 없다. 테스트는 이
    provider 를 override 해 ``KonepsCollectorService(http_get=fake)`` 를 넘긴다.
    """
    return KonepsCollectorService()

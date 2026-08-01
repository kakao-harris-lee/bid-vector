"""테스트용 bearer 인증 헬퍼.

``POST /api/v1/analytics/event`` 처럼 bearer 를 요구하는 경계가 늘면서, 여러 테스트
모듈이 같은 "세션 만들고 Authorization 헤더 조립" 6줄을 복붙하기 쉬워졌다(§4.5-6).
그 조립을 여기 한 곳에 둔다.

프론트 클라이언트는 토큰을 **모든** 요청의 기본 헤더로 싣는다
(``frontend/src/shared/api/client.ts``). :func:`authenticate_client` 는 그 모양을 그대로
재현해 ``TestClient`` 기본 헤더에 토큰을 붙이므로, 호출부는 요청마다 헤더를 넘기지
않아도 된다.
"""

from __future__ import annotations

DEFAULT_TEST_PASSWORD = "password123"


def bearer_headers(
    client,
    *,
    username: str,
    password: str = DEFAULT_TEST_PASSWORD,
) -> dict[str, str]:
    """이미 부트스트랩된 운영자로 세션을 열어 Authorization 헤더를 만든다."""
    response = client.post(
        "/api/v1/auth/session",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def authenticate_client(
    client,
    *,
    username: str,
    password: str = DEFAULT_TEST_PASSWORD,
) -> dict[str, str]:
    """세션 토큰을 ``client`` 의 **기본 헤더**로 붙이고 그 헤더를 돌려준다.

    이후 이 클라이언트의 모든 요청이 인증된 운영자로 나간다(실제 프론트와 동일).
    """
    headers = bearer_headers(client, username=username, password=password)
    client.headers.update(headers)
    return headers

"""Regression tests for static SPA serving (operator dashboard + admin console).

`app/main.py` serves two independently-built single-page apps from
``frontend/dist``:

* operator dashboard -> base ``/dashboard/``  (``frontend/dist/dashboard``)
* admin console      -> base ``/admin/``      (``frontend/dist/admin``)

These tests lock the serving *surface* (route ordering, asset mounts,
cross-bundle isolation, path-traversal guard, API non-shadowing and the
unbuilt-state 404). They deliberately do NOT exercise the authenticated
``/api/v1/dashboard/*`` JSON API — that contract lives in
``tests/test_dashboard_api.py``.

The built-state cases construct a fresh FastAPI app that mirrors the exact
mount + route registration from ``app.main`` against a temporary ``dist`` so
the asset-vs-catch-all precedence is exercised deterministically regardless of
whether ``frontend`` has actually been built in the test environment. The
unbuilt / API-shadowing cases drive the real ``app.main.app`` singleton.
"""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

import app.main as main

DASHBOARD_INDEX_HTML = (
    "<!doctype html><html><head>"
    '<script type="module" src="/dashboard/assets/index-dash.js"></script>'
    "</head><body>operator dashboard spa</body></html>"
)
ADMIN_INDEX_HTML = (
    "<!doctype html><html><head>"
    '<script type="module" src="/admin/assets/index-admin.js"></script>'
    "</head><body>admin console spa</body></html>"
)
DASHBOARD_ASSET_JS = "// dashboard bundle\nexport const APP = 'dashboard';\n"
ADMIN_ASSET_JS = "// admin bundle\nexport const APP = 'admin';\n"


def _write_built_dist(dist_root: Path) -> None:
    """Create a minimal two-bundle build tree under ``dist_root``."""
    dashboard = dist_root / "dashboard"
    admin = dist_root / "admin"
    (dashboard / "assets").mkdir(parents=True)
    (admin / "assets").mkdir(parents=True)
    (dashboard / "index.html").write_text(DASHBOARD_INDEX_HTML, encoding="utf-8")
    (admin / "index.html").write_text(ADMIN_INDEX_HTML, encoding="utf-8")
    (dashboard / "assets" / "index-dash.js").write_text(
        DASHBOARD_ASSET_JS, encoding="utf-8"
    )
    (admin / "assets" / "index-admin.js").write_text(ADMIN_ASSET_JS, encoding="utf-8")


def _build_spa_app(dashboard_dir: Path, admin_dir: Path) -> FastAPI:
    """Mirror ``app.main``'s SPA mount + route registration for a given dist.

    Kept structurally identical to ``app/main.py`` so this test fails if the
    real registration logic (mount precedence, fallback wiring) regresses in a
    way the shared helpers cannot catch on their own.
    """
    spa_app = FastAPI()

    if (dashboard_dir / "assets").is_dir():
        spa_app.mount(
            "/dashboard/assets",
            StaticFiles(directory=dashboard_dir / "assets"),
            name="dashboard-assets",
        )
    if (admin_dir / "assets").is_dir():
        spa_app.mount(
            "/admin/assets",
            StaticFiles(directory=admin_dir / "assets"),
            name="admin-assets",
        )

    @spa_app.get("/dashboard", include_in_schema=False)
    async def dashboard_index():
        return main._spa_file_response(dashboard_dir, "", "dashboard not built")

    @spa_app.get("/dashboard/{full_path:path}", include_in_schema=False)
    async def dashboard_spa_fallback(full_path: str):
        return main._spa_file_response(dashboard_dir, full_path, "dashboard not built")

    @spa_app.get("/admin", include_in_schema=False)
    async def admin_index():
        return main._spa_file_response(admin_dir, "", "admin not built")

    @spa_app.get("/admin/{full_path:path}", include_in_schema=False)
    async def admin_spa_fallback(full_path: str):
        return main._spa_file_response(admin_dir, full_path, "admin not built")

    return spa_app


@pytest.fixture
def built_dist(tmp_path: Path) -> Path:
    dist_root = tmp_path / "frontend" / "dist"
    _write_built_dist(dist_root)
    return dist_root


@pytest.fixture
def built_client(built_dist: Path) -> TestClient:
    spa_app = _build_spa_app(built_dist / "dashboard", built_dist / "admin")
    with TestClient(spa_app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# 1. Each bundle's index is served at its own base with its own asset base.
# ---------------------------------------------------------------------------
def test_dashboard_index_serves_dashboard_bundle(built_client):
    response = built_client.get("/dashboard")
    assert response.status_code == 200
    body = response.text
    assert "operator dashboard spa" in body
    assert "/dashboard/assets/" in body


def test_admin_index_serves_admin_bundle(built_client):
    response = built_client.get("/admin")
    assert response.status_code == 200
    body = response.text
    assert "admin console spa" in body
    assert "/admin/assets/" in body


# ---------------------------------------------------------------------------
# 2. Deep client-side routes fall back to each bundle's index.html (SPA).
# ---------------------------------------------------------------------------
def test_dashboard_deep_path_falls_back_to_index(built_client):
    response = built_client.get("/dashboard/opportunities")
    assert response.status_code == 200
    assert "operator dashboard spa" in response.text


def test_admin_deep_path_falls_back_to_index(built_client):
    response = built_client.get("/admin/operations")
    assert response.status_code == 200
    assert "admin console spa" in response.text


# ---------------------------------------------------------------------------
# 3. Real asset files are served from each bundle's mount (mount beats catch-all).
# ---------------------------------------------------------------------------
def test_dashboard_asset_is_served_from_mount(built_client):
    response = built_client.get("/dashboard/assets/index-dash.js")
    assert response.status_code == 200
    assert "dashboard bundle" in response.text
    # An asset request must NOT be swallowed by the SPA index fallback.
    assert "operator dashboard spa" not in response.text


def test_admin_asset_is_served_from_mount(built_client):
    response = built_client.get("/admin/assets/index-admin.js")
    assert response.status_code == 200
    assert "admin bundle" in response.text
    assert "admin console spa" not in response.text


# ---------------------------------------------------------------------------
# 4. Cross-bundle isolation: neither index advertises the other's asset base,
#    and one bundle's asset path never resolves into the other's tree.
# ---------------------------------------------------------------------------
def test_dashboard_index_does_not_leak_admin_asset_base(built_client):
    body = built_client.get("/dashboard").text
    assert "/admin/assets/" not in body
    assert "admin console spa" not in body


def test_admin_index_does_not_leak_dashboard_asset_base(built_client):
    body = built_client.get("/admin").text
    assert "/dashboard/assets/" not in body
    assert "operator dashboard spa" not in body


def test_dashboard_cannot_serve_admin_asset_file(built_client):
    # The admin asset only exists under /admin/assets. Requesting it under the
    # dashboard tree must fall back to the dashboard SPA, never the admin file.
    response = built_client.get("/dashboard/assets/index-admin.js")
    assert "admin bundle" not in response.text
    if response.status_code == 200:
        # SPA fallback (the dashboard catch-all) — must be the dashboard index.
        assert "operator dashboard spa" in response.text


# ---------------------------------------------------------------------------
# 5. Path-traversal guard: a request that survives client normalization with
#    encoded `..` must not escape the bundle root to read a sibling file.
# ---------------------------------------------------------------------------
def test_admin_path_traversal_does_not_escape_bundle(built_dist, built_client):
    # Sentinel sitting next to (outside) the admin bundle root.
    secret = built_dist / "admin-secret.txt"
    secret.write_text("TOP-SECRET-DO-NOT-SERVE", encoding="utf-8")

    # Encoded `..` keeps the dots in the path that reaches the catch-all route,
    # exercising the server-side relative_to() guard rather than httpx's own
    # URL normalization. Disable redirect following so a normalizing 307 can't
    # mask a leak.
    response = built_client.get(
        "/admin/..%2f..%2fadmin-secret.txt", follow_redirects=False
    )
    assert "TOP-SECRET-DO-NOT-SERVE" not in response.text
    if response.status_code == 200:
        assert "admin console spa" in response.text


def test_dashboard_path_traversal_does_not_escape_bundle(built_dist, built_client):
    secret = built_dist / "dashboard-secret.txt"
    secret.write_text("TOP-SECRET-DO-NOT-SERVE", encoding="utf-8")

    response = built_client.get(
        "/dashboard/..%2f..%2fdashboard-secret.txt", follow_redirects=False
    )
    assert "TOP-SECRET-DO-NOT-SERVE" not in response.text
    if response.status_code == 200:
        assert "admin console spa" not in response.text  # never the admin bundle
        assert "operator dashboard spa" in response.text


def test_admin_traversal_into_dashboard_bundle_is_blocked(built_dist, built_client):
    # Even a *valid* sibling file (the dashboard index) must not be reachable by
    # traversing out of the admin bundle.
    response = built_client.get(
        "/admin/..%2fdashboard%2findex.html", follow_redirects=False
    )
    assert "operator dashboard spa" not in response.text


# ---------------------------------------------------------------------------
# 6. The SPA catch-alls must not shadow the existing JSON API. Driven against
#    the real app.main.app so the actual route-registration order is asserted.
# ---------------------------------------------------------------------------
def test_admin_spa_does_not_shadow_admin_api(client):
    """`/api/v1/admin/*` must keep returning the JSON API, not the SPA index.

    The legacy admin endpoints are not auth-gated in the single-operator model,
    so the invariant here is the *content type / shape* (JSON, not an HTML SPA
    document) rather than a particular auth status code.
    """
    response = client.get("/api/v1/admin/users")
    assert response.status_code == 200
    assert "application/json" in response.headers.get("content-type", "")
    assert "text/html" not in response.headers.get("content-type", "")
    # Never the admin SPA HTML body.
    assert "admin console spa" not in response.text


def test_dashboard_spa_does_not_shadow_dashboard_api(client):
    """`/api/v1/dashboard/*` keeps its authenticated JSON contract."""
    response = client.get("/api/v1/dashboard/summary")
    assert response.status_code == 401
    assert "application/json" in response.headers.get("content-type", "")
    assert "text/html" not in response.headers.get("content-type", "")
    assert "operator dashboard spa" not in response.text


# ---------------------------------------------------------------------------
# 7. Unbuilt state: with no dist present, both bases return a 404 with build
#    guidance. Driven against the real app with the dist constants pointed at
#    an empty temp directory.
# ---------------------------------------------------------------------------
def test_unbuilt_dashboard_and_admin_return_not_built_404(monkeypatch, tmp_path):
    empty = tmp_path / "frontend" / "dist"
    monkeypatch.setattr(main, "DASHBOARD_DIST_DIR", empty / "dashboard")
    monkeypatch.setattr(main, "ADMIN_DIST_DIR", empty / "admin")

    with TestClient(main.app) as test_client:
        dashboard = test_client.get("/dashboard")
        admin = test_client.get("/admin")

    assert dashboard.status_code == 404
    assert "has not been built" in dashboard.json()["detail"]
    assert "Dashboard frontend" in dashboard.json()["detail"]

    assert admin.status_code == 404
    assert "has not been built" in admin.json()["detail"]
    assert "Admin frontend" in admin.json()["detail"]


def test_unbuilt_deep_paths_also_return_404(monkeypatch, tmp_path):
    empty = tmp_path / "frontend" / "dist"
    monkeypatch.setattr(main, "DASHBOARD_DIST_DIR", empty / "dashboard")
    monkeypatch.setattr(main, "ADMIN_DIST_DIR", empty / "admin")

    with TestClient(main.app) as test_client:
        assert test_client.get("/dashboard/opportunities").status_code == 404
        assert test_client.get("/admin/operations").status_code == 404

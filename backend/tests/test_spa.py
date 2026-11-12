from pathlib import Path

import pytest

from app.config import settings


def test_unknown_api_path_is_json_404_not_the_page(client):
    for method in ("get", "post", "put", "patch", "delete"):
        r = getattr(client, method)("/api/no-such-endpoint")
        assert r.status_code == 404, method
        assert r.headers["content-type"].startswith("application/json")
        assert r.json()["detail"] == "no such API endpoint"


def test_api_replies_are_not_cached(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    assert client.get("/api/no-such-endpoint").headers["cache-control"] == "no-store"


def test_routes_with_their_own_cache_header_keep_it(client):
    from app.main import app
    from fastapi import Response

    @app.get("/api/__test_cached")
    def cached():
        return Response(b"x", headers={"Cache-Control": "private, max-age=60"})

    # the catch-all was registered first, so move the test route ahead of it
    app.router.routes.insert(0, app.router.routes.pop())
    try:
        assert client.get("/api/__test_cached").headers["cache-control"] == "private, max-age=60"
    finally:
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", "") != "/api/__test_cached"]


static = Path(settings.static_dir)


@pytest.mark.skipif(not (static / "index.html").is_file(), reason="needs the built frontend")
def test_spa_page_and_no_escape_from_build_folder(client):
    page = client.get("/variance")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert page.headers["cache-control"] == "no-cache"
    # ../ out of the build folder gets the page, never the file
    for p in ("/..%2f..%2fapp%2fconfig.py", "/%2e%2e/%2e%2e/app/config.py"):
        r = client.get(p)
        assert "admin_password" not in r.text, p

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .audit import AuditMiddleware, install_guards
from .config import settings
from . import tenancy  # noqa: F401 - registers the tenant and business filters
from .db import Base, SessionLocal, add_missing_columns, engine, upgrade_tenancy
from .routers import auth, businesses, counts, deliveries, groups, imports, items, mfa, movements, platform, recipes, search, swype_items, users, variance
from .routers import settings as settings_router
from .seed import seed
from .services import maintenance, swype_auto


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    add_missing_columns(engine)
    upgrade_tenancy(engine)  # a database from before tenants: keys that include the business
    install_guards(engine)
    with SessionLocal() as db:
        seed(db)
    tasks = [asyncio.create_task(swype_auto.loop()), asyncio.create_task(maintenance.loop())] if settings.scheduler_enabled else []
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Bar reconciliation portal", version="0.1.0", lifespan=lifespan)
for r in (auth.router, mfa.router, items.router, counts.router, movements.router, imports.router, search.router, settings_router.router,
          recipes.router, variance.router, groups.router, swype_items.router, deliveries.router, users.router, users.audit_router, businesses.router, platform.router):
    app.include_router(r)
app.add_middleware(AuditMiddleware)


class NoStoreAPI:
    """API replies are live data: tell the browser not to keep them. Without a Cache-Control header a browser may
    reuse an old reply heuristically, e.g. one from before a deploy added the route. Routes that set their own
    header (photos, receipts, logos) keep it."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            return await self.app(scope, receive, send)

        async def send_wrapped(msg):
            if msg["type"] == "http.response.start":
                headers = list(msg.get("headers", []))
                if not any(k.lower() == b"cache-control" for k, _ in headers):
                    headers.append((b"cache-control", b"no-store"))
                msg = {**msg, "headers": headers}
            await send(msg)

        await self.app(scope, receive, send_wrapped)


app.add_middleware(NoStoreAPI)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
def api_not_found(path: str):
    # registered after every router, so it only catches /api/ paths no router has; without it the SPA fallback below
    # would answer them with index.html and a 200
    raise HTTPException(404, "no such API endpoint")


static = Path(settings.static_dir)
if static.is_dir():
    app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")
    root = static.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = (static / path).resolve()
        # only files inside the build folder (no ../ out of it)
        if path and f.is_relative_to(root) and f.is_file():
            return FileResponse(f)
        # the page itself is checked on every load, so a deploy's new bundle is picked up at once
        return FileResponse(static / "index.html", headers={"Cache-Control": "no-cache"})

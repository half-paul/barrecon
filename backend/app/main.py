import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .audit import AuditMiddleware, install_guards
from .config import settings
from .db import Base, SessionLocal, add_missing_columns, engine
from .routers import auth, counts, deliveries, groups, imports, items, movements, recipes, search, swype_items, users, variance
from .routers import settings as settings_router
from .seed import seed
from .services import maintenance, swype_auto


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    add_missing_columns(engine)
    install_guards(engine)
    with SessionLocal() as db:
        seed(db)
    tasks = [asyncio.create_task(swype_auto.loop()), asyncio.create_task(maintenance.loop())] if settings.scheduler_enabled else []
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Bar reconciliation portal", version="0.1.0", lifespan=lifespan)
for r in (auth.router, items.router, counts.router, movements.router, imports.router, search.router, settings_router.router,
          recipes.router, variance.router, groups.router, swype_items.router, deliveries.router, users.router, users.audit_router):
    app.include_router(r)
app.add_middleware(AuditMiddleware)


@app.get("/api/health")
def health():
    return {"ok": True}


static = Path(settings.static_dir)
if static.is_dir():
    app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = static / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(static / "index.html")

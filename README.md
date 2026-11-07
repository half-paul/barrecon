# Bar reconciliation portal

Read-only reconciliation of SwypePOS sales against the bar's own inventory counts.
Phase 1 (this repo) is the inventory side: item master, weekly counts, deliveries and
stock movements, spreadsheet round-trip, and the one-time import of the existing
"To Order Inventory List" workbook. Phases 2-3 add recipes, the Swype sales import and
the variance report. See docs/bar-inventory-portal-spec.md for the design and
HANDOFF.md for where the build stands.

## Run it (Windows or macOS with Docker Desktop)

    cp .env.example .env        # edit ADMIN_PASSWORD and SESSION_SECRET
    docker compose up -d --build

Open http://localhost:8080 and log in as `admin` with ADMIN_PASSWORD (only used to create that
first login; then add people under Setup -> Users and change your password under My account). The database lives in a
named Docker volume; nightly pg_dump files land in ./backups (point BACKUP_DIR in
.env at a Google Drive for Desktop folder to get them off the PC).

First steps in the app: Imports -> Choose workbook -> pick the inventory xlsx -> review
the preview -> Import. Then Items -> confirm the assumed pack size, case size and cost
(docs/items-master-draft.xlsx lists each assumption).

## Develop (macOS)

    cd backend
    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements-dev.txt
    pytest                                   # SQLite in memory
    DATABASE_URL=sqlite:///./dev.db ADMIN_PASSWORD=demo SESSION_SECRET=dev python -m uvicorn app.main:app --reload --port 8000

    cd frontend && npm install && npm run dev      # proxies /api to :8000

npm run build writes into backend/app/static, which the API serves in the Docker image.

## Layout

    backend/app/models.py                  schema (UUID string ids; SQLite for tests, Postgres for real)
    backend/app/units.py                   ml/g/each conversion; oz is a setting (28.4131 Canadian default)
    backend/app/services/inventory.py      rules shared by every entry path (supersede, idempotent movements)
    backend/app/services/xlsx_io.py        Items/Counts/Movements templates and imports
    backend/app/services/legacy_import.py  the old workbook importer
    backend/app/services/recipes.py        recipes per Swype item #, versioned; pour drafts from Swype names
    backend/app/services/variance.py       sold x recipe vs count-to-count usage, flags >= threshold
    backend/app/auth.py                    logins, sessions, per-area permissions (AREAS)
    backend/app/audit.py                   audit middleware: every API change, with who made it
    backend/app/routers/                   /api/auth /api/users /api/audit /api/items /api/counts /api/movements /api/imports /api/settings /api/recipes /api/variance
    backend/tests/                         pytest; fixture_legacy_workbook.xlsx is the real sample file
    frontend/src/                          React + Vite + Tailwind, one file per screen
    docs/                                  spec, draft item master, sample workbook

Schema is created with Base.metadata.create_all at startup. Introduce Alembic before
the first schema change that must preserve data.

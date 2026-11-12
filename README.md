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

## Tenants and businesses

The portal serves several tenants (customers), each with one or more businesses (a bar, a restaurant) and its own logins.

- **Platform console** (`/platform`): log in as `platform` (PLATFORM_ADMIN_PASSWORD, or ADMIN_PASSWORD if that is empty).
  Add tenants with their first admin, set limits (businesses, active logins, Claude tokens per month), suspend and
  reactivate, see usage per tenant and business (Claude tokens with an estimated cost, logins, counts, deliveries,
  storage) and download the monthly usage report (CSV/XLSX). Platform admins see no tenant's stock or sales.
- **Tenant admin** (Setup -> Businesses, Setup -> Users): add businesses with address, phone, logo and Swype login;
  give each person access business by business, with a level per area in each. A new business can start from a copy
  of another one's stock items, groups and recipes.
- **Everyone**: the business's logo and name are at the top left of every page; with access to more than one, that is
  also where you switch. Stock items, groups, Swype items, recipes, counts, deliveries, sales and settings all belong
  to one business; SKUs and Swype numbers only need to be unique inside it.
- Usernames and emails are unique across the whole platform (one login page for all tenants).

`app/tenancy.py` keeps every request inside its tenant and business: reads, updates and deletes are filtered and new
rows are stamped, whatever a query forgot. A database from before tenants is upgraded in place at startup
(`db.upgrade_tenancy` + `seed`): the bar becomes the first business of a first tenant, admins become its admins, and
everyone else keeps their permissions as a membership of that business. Back up first (`pg_dump`); there is no downgrade.

## Logins

Two-step login (an authenticator app) is optional: anyone can turn it on or off under My account, and once on, every
password login asks for a code. Set ADMIN_MFA_REQUIRED=true to make it mandatory for admins: their next password login
sets it up, and they can't turn it off. PIN logins skip it and stay limited to counts and deliveries.

Set MAILGUN_API_KEY + MAILGUN_DOMAIN (or the SMTP values), MAIL_FROM and PUBLIC_URL in .env to turn on email. Without them nothing is emailed: Add user asks for a
password instead of sending an invite, and "Forgot your password?" is hidden.

Lost phone, in the order to try: a recovery code; "Email me a code" on the code screen (off for 24 hours after a
password reset by email, so an inbox alone can't take over an admin login); another admin's "Reset 2-step" under
Setup -> Users.

No admin can log in: `docker compose exec api python -m app.reset_mfa <username>`, then log in and set up a new
authenticator. Changing SESSION_SECRET has the same effect on authenticators as losing them, so don't rotate it casually.

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
    backend/app/tenancy.py                 tenant and business scoping of every query and insert
    backend/app/routers/businesses.py      a tenant's businesses: details, logo, Swype login, who may work there
    backend/app/routers/platform.py        the platform console API: tenants, limits, usage, reports
    backend/app/services/usage.py          Claude token metering, estimated cost, monthly cap
    backend/app/auth.py                    logins, sessions, per-area permissions (AREAS) in the session's business
    backend/app/services/logins.py         optional MFA, recovery codes, one-time email tokens
    backend/app/services/mail.py           Mailgun/SMTP sending and email wording
    backend/app/audit.py                   audit middleware: every API change, with who made it
    backend/app/routers/                   /api/auth /api/users /api/audit /api/items /api/counts /api/movements /api/imports /api/settings /api/recipes /api/variance
    backend/tests/                         pytest; fixture_legacy_workbook.xlsx is the real sample file
    frontend/src/                          React + Vite + Tailwind, one file per screen
    docs/                                  spec, draft item master, sample workbook

Schema is created with Base.metadata.create_all at startup. Introduce Alembic before
the first schema change that must preserve data.

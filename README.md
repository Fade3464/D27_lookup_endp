# D27 Campaign Desk

Authenticated suppression checker using Django 5.2 LTS, PostgreSQL 17,
PgBouncer transaction pooling, React, TypeScript, Tailwind CSS and shadcn/ui
components. The original standalone `lookup.py` CLI remains available.

## Start locally

Requires Docker with Compose. From this directory:

```bash
python3 init_env.py
docker compose up --build -d
docker compose exec backend python manage.py createsuperuser
```

Open **http://localhost:18080** and sign in with that account. Django admin is at
**http://localhost:18080/admin/**. The first startup runs migrations, collects
admin static files, and seeds campaigns `21625` / D27 (1) and `21627` / D27 (2).
There are no default credentials or public registration.

`init_env.py` generates a private `.env` with random secrets and never overwrites
an existing file. Leave `.env` out of version control. `docker compose down`
stops the app and keeps records. Do not use `down -v` unless you intend to delete
the database. Back up the PostgreSQL volume for durable operation.

## Public individual D27 check

Open **http://localhost:18080/lookup-record** without signing in, or use
**Individual D27 Check** in the user portal. Enter a 10-digit number; formatting
and a leading US `+1` are normalized. Both campaigns must explicitly return
`accepted=1` to show **Allowed for D27**. Either campaign returning `0`
shows **Not allowed for D27**. Service failures show a retry message
and never grant an allowance.

The public page uses `GET /api/individual/session/` for a CSRF token and
`POST /api/individual/check/` with `{"ani":"2345678901"}`. Successful decisions
return HTTP 200; invalid input returns 400, upstream rate limits 429, other
upstream failures 502, and an active global safety pause 503. The public endpoint has no application or Nginx request rate limit. Keep Django
private behind the host proxy. Individual checks run on demand, separately from CSV queues,
and respect an existing global safety pause. They do not advance CSV progress.

Attempts and raw responses are retained in the read-only **Individual lookups**
Django admin view, with an optional signed-in user. There is no public history
or record retrieval endpoint. Existing portal, CSV, and history APIs still
require authentication. The design preview remains offline.

## Production deployment

Use the standalone [production Compose stack](compose.production.yaml) with
**host Nginx**. The [deployment guide](docs/production-deployment.md) covers TLS,
configuration, startup, verification, updates, and backups. The host site config
is [deploy/nginx/d27-production.conf](deploy/nginx/d27-production.conf).
For Cloudflare proxying, use the [complete Cloudflare update commands](docs/cloudflare-deployment.md).

## UI design review

Open **http://localhost:18080/design-preview** to review the Apple-inspired UI
without an account. The preview uses fictional, in-memory records and makes no
API requests. Its selector shows accepted, suppressed, error and empty states;
the sign-in screen, history and account layout are also available for review.

The community Apple Design skill is downloaded at `skills/apple-design/` and
referenced by `AGENTS.md` for future design work. Design decisions, measured
contrast ratios and verification limits are recorded in `docs/ui-design.md`.
The real app at `/` retains its existing authentication and API integration.

## What gets recorded

The **CSV Queue** dashboard now accepts CSV uploads, cleans the first column,
processes each user's queue, tracks progress, and prepares downloads. See
[CSV queue operation and deployment](docs/csv-queue.md) for cleanup rules,
pause/resume, safety stops, Discord, storage and large-file validation.

The fixed upstream endpoint is:

```bash
curl -sS 'https://marstools.co/d27suppressioncheck/ping.php' \
  -H 'Content-Type: application/json' \
  -d '{"ani":"2345678901"}'
```

The endpoint accepts **only ANI** and checks both campaigns. Selecting a campaign
in the app selects its result; it does not change the upstream request. New
campaign IDs must be supported by that service before they can return a result.

Each submitted lookup creates a record **before** calling the service. Records
contain the user, normalized ANI, campaign ID and historical label, timestamps,
HTTP status, outgoing JSON, complete parsed response, raw response text, selected
campaign result, and any failure. The supplied example is retained in
`backend/checker/tests/upstream.py` as a contract fixture, not as a live record.

`accepted: 1` or `"1"` means good to go; `0` or `"0"` means not allowed. A result
is accepted only when the wrapper reports `ok: true`, both HTTP statuses succeed,
the ANI matches, and the campaign result is present. Timeouts, invalid JSON,
missing campaigns and unrecognized responses are saved as failed checks. Each
HTTP lookup request returns `201` for the created audit record; inspect its
`status` and `error` to determine the lookup outcome. Invalid user input returns
`400` before any lookup is made. There are no automatic retries that might
silently duplicate requests. If a worker is killed, its attempt stays pending
for administrator review. Responses are capped at 1 MB; oversized responses are
marked failed and their retained raw text is truncated to the cap plus one byte.

## Authentication and administration

- Server-side Django sessions; HttpOnly, SameSite cookies and CSRF checks for
  login and every state-changing API call. Secure cookies are enabled outside
  local debug mode. Passwords use Django's password hashing and validators.
- Five failed logins lock a username/IP pair for 15 minutes, including admin
  login. Nginx also limits login and lookup request rates.
- Administrators create, deactivate and reset passwords for users through
  Django admin. Password changes are available under **My account**; they
  invalidate the user's other sessions. There is no email reset flow.
- Regular users see only their own records. Staff need the explicit
  `checker | lookup | Can view lookup` permission to see all users' records.
  Superusers have all permissions. Django groups can assign reviewer access.
- Campaign management uses Django's built-in model permissions. Campaign IDs
  cannot be edited after creation; campaigns can be deactivated.
- Lookup records are read-only in Django admin, including for superusers.
  User/campaign deletion is protected when referenced by a lookup. This is
  application-level protection, not a tamper-proof database or retention policy.

## Database and PgBouncer

All runtime database operations, **including writes, sessions and admin**, use
`pgbouncer:6432`, which forwards to PostgreSQL in transaction pooling mode. There
is one database alias so a record is immediately readable after a successful
write. No replicas or eventual consistency are involved.

Django disables server-side cursors and psycopg automatic prepared statements
for compatibility with transaction pooling. Connections close at the end of
each request; PgBouncer owns the server pool. No database transaction stays open
while waiting for the HTTP lookup. Migrations use a separate startup container
connecting directly to PostgreSQL for DDL.

The DB password is read from `.env`. PgBouncer generates a mode-600 credential
file inside its container and authenticates with SCRAM. DB names/usernames must
use letters, digits or underscores; passwords must use URL-safe token characters.
Only Nginx exposes a host port, bound to loopback by default. PostgreSQL,
PgBouncer and Django are internal Docker services.

## API

All routes except the session bootstrap and login require authentication.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/auth/session/` | Current user and CSRF token |
| POST | `/api/auth/login/` | `{ "username": "…", "password": "…" }` |
| POST | `/api/auth/logout/` | End session |
| POST | `/api/auth/password/` | `{ "old_password": "…", "new_password": "…" }` |
| GET | `/api/campaigns/` | Active campaigns |
| POST | `/api/lookups/` | `{ "ani": "2345678901", "campaign_id": "21625" }` |
| GET | `/api/history/` | Paginated records; optional `ani`, `status`, `campaign`, `page` |
| GET | `/api/history/<uuid>/` | Full saved response; same access rules as history |

Browser requests include the session cookie and `X-CSRFToken`. Login rotates
the token; the client uses the token returned by the login response.

## Checks and development

The backend contract/security tests use SQLite and mock the external endpoint:

```bash
uv venv .venv
uv pip install --python .venv/bin/python -r backend/requirements.lock
.venv/bin/python backend/manage.py test checker --settings=config.settings_test
.venv/bin/python backend/manage.py makemigrations --check --dry-run --settings=config.settings_test
cd frontend
pnpm install --frozen-lockfile
pnpm build
```

Run the same tests against PostgreSQL directly in Docker (the test runner
creates and drops a separate database, which is not mapped in PgBouncer):

```bash
docker compose exec -e DB_HOST=postgres -e DB_PORT=5432 backend python manage.py test checker
```

For Vite development, start Django on port 8000 with local environment variables
configured, then run `pnpm dev` in `frontend`. Vite proxies `/api`, `/admin` and
`/static` to Django. Add the Vite origin to `CSRF_TRUSTED_ORIGINS` for admin forms.
For example, to run Django against the Docker database, use
`docker compose stop backend` then
`docker compose run --rm -p 127.0.0.1:8000:8000 backend python manage.py runserver 0.0.0.0:8000`.

## Production setup

The supplied Compose configuration is a **local HTTP setup**. Before exposing
it, configure HTTPS, set `DJANGO_DEBUG=0`, list your domain in
`DJANGO_ALLOWED_HOSTS`, and set `CSRF_TRUSTED_ORIGINS` to its HTTPS origin. Keep
backend/database ports private. Configure Nginx or a trusted edge proxy with
TLS. If TLS terminates upstream of this Nginx, configure that trusted path to
set `X-Forwarded-Proto` to the verified original scheme instead of `$scheme`;
strip any client-supplied values. Then set `TRUST_PROXY_HTTPS=1` and
`SECURE_SSL_REDIRECT=1`. Never trust arbitrary forwarded headers.

Do not publish using the local debug settings. Validate production settings with
`docker compose exec backend python manage.py check --deploy`. Arrange database
backups, access controls and a retention policy appropriate for stored phone
numbers. No hosted deployment is configured.

Technical references: [Django transaction pooling](https://docs.djangoproject.com/en/5.2/ref/databases/#transaction-pooling-and-server-side-cursors),
[PgBouncer configuration](https://www.pgbouncer.org/config.html),
[shadcn/ui with Vite](https://ui.shadcn.com/docs/installation/vite).

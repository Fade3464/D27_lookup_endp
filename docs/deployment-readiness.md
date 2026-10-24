# Deployment verification — 21 October 2026

CSV implementation is running locally at http://localhost:18080. All three
workers are running, migrations are applied, and Nginx configuration validates.

Production deployment files are now available in
[the host-Nginx deployment guide](production-deployment.md). Its standalone
Compose stack contains no Nginx service. An isolated verification stack passed
startup, migrations, frontend publishing, HTTPS proxy/session checks, per-IP
rate limiting, authenticated exports served directly by Nginx, and database
backup/restore into an empty test database. TLS routing
was tested with a temporary certificate; issuance for the real domain remains
a deployment step. The existing local development environment remains unchanged.

Verified:

- Updated PostgreSQL suite: 51 tests discovered; 50 passed, including competing
  worker claims and public individual checks. The opt-in large-file test was
  skipped in this run. The earlier 500,000-number import/export test passed;
  CSV processing/export code was unchanged by the individual-lookup feature.
- Updated quick suite: 51 tests discovered; 49 passed, with the PostgreSQL-only
  and opt-in large-file tests skipped in the SQLite run.
- Production frontend build and TypeScript passed.
- Public `/lookup-record` component checks passed for anonymous rendering,
  normalization, decisions, stale-result clearing, CSRF submission and service
  failures. Backend tests cover both-campaign verdicts, invalid response schemas,
  private audit records, safety pauses, and authentication boundaries.
- Component checks passed for CSV progress, pause/next-file modal, keeping a
  queue paused, starting the next file, tracking, and preview isolation.
- Actual authenticated HTTP export download passed through Nginx and PgBouncer.
  Direct internal paths returned 404, and anonymous downloads/API access 401.
- Public-page HTTP checks through Nginx passed for anonymous access, private
  history, CSRF failures, invalid input, and the per-IP rate limit. One live
  check using the supplied sample number returned `allowed`; its complete
  response was retained in the private individual audit table.
- No lookup API or production Discord webhook was called by the batch tests.
  The large-file test simulated classification after importing all 500,000 rows.

The **current environment is not ready for public production deployment**.
Django's deployment check reports five warnings from the local HTTP/debug
configuration: DEBUG is enabled, SSL redirect and secure cookies are disabled,
and HSTS is not enabled. Configure HTTPS and trusted proxy handling, set
`DJANGO_DEBUG=0`, configure production hosts/CSRF origins, and rerun
`python manage.py check --deploy`. Configure `DISCORD_WEBHOOK_URL` and test a
notification in the production environment. Back up both database and file
volumes. Browser visual/assistive-technology verification remains outstanding.

See [CSV queue operation](csv-queue.md) for deployment commands, export format,
storage requirements and the sequential lookup throughput limit.

Deployment configuration update: production now uses `d27desk.peoplegraph.co`,
checkout `/srv/D27_lookup_endp`, and loopback-only Gunicorn port `18000`. The
public lookup request limiter has been removed from both development and
production Nginx configs. Earlier rate-limit test results describe the previous
configuration. Upstream HTTP 429 handling is retained.
Updated Nginx configs passed syntax validation. A 30-request public lookup burst
reached a mock backend without throttling; login rate limiting remained active.
No live lookup API calls were made for this configuration change.

Queued-job deletion verification: 58 PostgreSQL tests discovered, 57 passed,
with the opt-in large-file test skipped. This includes a concurrent worker
claim versus deletion test. The quick SQLite suite passed 55 tests with three
PostgreSQL/large-file skips. Frontend build and component checks passed for
delete/cancel/Escape, conflicts, dashboard refresh, pause/safety dialogs, and
preview isolation. The new status migration is `batches.0002_alter_batch_status`.
Deleted jobs retain their audit data and are excluded from user APIs and quota.
No live lookup or Discord requests were made for these checks.

Partial-progress export verification — 24 October 2026: all **75 PostgreSQL
tests passed**, including concurrent request/publication, single-worker claims,
and the 500,000-row import/partial-export/resume/final-export case. That scale
test froze a 300,000-result snapshot, simulated completion of the remaining
200,000 rows, and verified both the unchanged partial CSV and final full CSV.
No live lookup requests were made.

The production frontend build and component checks passed for preparation,
polling, download links, retained snapshots after resume, newer snapshots,
retry, empty-result/pausing guards, and offline preview behavior. An actual
local Nginx HTTP check passed for preparation by the background file worker,
exact CSV contents, repeated cached downloads, owner authentication,
internal-path denial, and preserving the paused state. Temporary HTTP fixtures
were removed. Deploy migration `batches.0003_partialexport` and rebuild/restart
the backend/file worker and frontend using the production update steps.

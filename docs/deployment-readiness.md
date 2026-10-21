# Deployment verification — 21 October 2026

CSV implementation is running locally at http://localhost:18080. All three
workers are running, migrations are applied, and Nginx configuration validates.

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

# CSV lookup queues

The dashboard is the **CSV Queue** tab. Upload a UTF-8 CSV up to 100 MB. Cleanup
runs in a file worker before a valid file enters that user's FIFO queue. Every
user has a separate queue; the lookup engine shares a global rate gate and
serves eligible users in round-robin order. It allows only one upstream request
in flight across the deployment, even if multiple lookup workers are started.

## Cleanup and decisions

Only the first column is used. Spaces, dashes, parentheses, dotted phone
formatting and a leading US country code 1 are normalized. Exactly ten ASCII
digits must remain. Words, formulas, scientific notation, decimal values,
unsupported international numbers and other garbage are rejected. Other
columns are ignored. Headers and blank rows count as skipped rows. Duplicate
numbers are deliberately retained as separate input records. If no valid number
exists, the file is marked invalid and never enters the lookup queue. Malformed
or non-UTF-8 CSVs are rejected as whole files, including any partial import.

For each number there is one POST to the supplied endpoint, which returns both
campaigns. Both wrappers, HTTP statuses, ANI, campaign IDs and accepted values
must validate. Both accepted=1 means sqGOOD. Either or both accepted=0 means
sqBAD; mixed decisions are counted separately as a subset of bad. An error is
never classified as bad. Errors stay unresolved and retain their raw response
and attempt history until retried successfully.

The progress bar is **resolved / valid rows**. Error attempts do not inflate it
to 100%. Source row numbers, cleaned/skipped counts, user ownership, actions,
attempt counts, latest raw responses and failed-attempt responses are retained.
Use **Track** to inspect them without loading an entire file into the browser.

## Pause, recovery and safety

Pause records the request immediately, finishes the current HTTP request, and
commits that result before stopping. The dashboard then asks whether to start
the next file. Closing the modal or choosing Keep Queue Paused leaves that user's
queue disabled. Other users' queues continue. Resume retains completed results;
resuming while another file is running does not preempt that file. Failed rows
are retried explicitly with Retry Errors. A file with unresolved errors cannot
publish a completed export.

HTTP 429 or a malformed/unexpected/authentication response stops all lookup
queues immediately. Ten consecutive recoverable transport/server failures also
stop all queues. A successful good or bad decision resets that global streak.
New imports can finish cleanup during a stop, but cannot issue lookups.

Only staff with `batches.resume_safety_stop` can resume after review. Existing
manual pauses remain paused. Safety recovery does not silently retry failed rows.
Lookup worker leases fence late results. An expired lease causes a safety stop
rather than silently issuing another possibly duplicated external request.
Because the external endpoint has no idempotency-key contract, a crash after an
HTTP request but before saving its response can require repeating that request
after staff review; exactly-once external delivery cannot be guaranteed.

## Discord

Set `DISCORD_WEBHOOK_URL` in production `.env`. A dedicated notification worker
uses a durable outbox and retries failed or rate-limited Discord deliveries.
An unset URL is reported as unconfigured; it never prevents a safety stop.
Alerts contain the stop reason and batch identifier, not phone numbers,
filenames, raw API responses, credentials or mentions. No production webhook is
called by automated tests.

## Exports and scale

An export is built once, after every valid number has a good or bad decision.
It has exactly two columns, `sqGOOD,sqBAD`. Each valid input row produces one
output row: its number appears in the relevant column and the other is blank.
Order and duplicates are preserved. For example:

```csv
sqGOOD,sqBAD
2345678901,
,2345678902
```

Import uses batched inserts, default 5,000 rows. Queue lookup uses the indexed
batch/status/sequence key. Export uses keyset pages of 5,000 numbers, never
OFFSET, raw-response filtering, a join, or an unbounded ORM iterator. This also
avoids buffering an entire result set when PgBouncer disables server cursors.
Export files are atomically published, row-count checked and SHA-256 recorded.
Repeated downloads authorize ownership with a small metadata query, then use
Nginx internal redirects to serve the existing file. They do not requery 500,000
numbers, regenerate exports or stream through a Gunicorn worker.

Defaults: 1,000,000 valid numbers / 2,000,000 source records per file; 10 unfinished
files per user; 1 second minimum between request starts. At one request per
second, 500,000 lookups take **at least 5.8 days**, plus network latency and other
users' work. The large-file test verifies import/export mechanics, not 500,000
live API calls or a high-throughput upstream SLA. Raw responses can consume
hundreds of MB per large file, so size PostgreSQL and storage accordingly.

## Run and verify

```bash
docker compose up --build -d
docker compose ps
docker compose logs --tail 50 lookup_worker file_worker alert_worker
docker compose run --rm --no-deps -e DB_HOST=postgres -e DB_PORT=5432 \
  -e RUN_LARGE_BATCH_TESTS=1 backend python manage.py test checker batches --noinput
```

`lookup_worker`, `file_worker`, and `alert_worker` are separate processes. All
runtime writes use PgBouncer. Migrations use the direct PostgreSQL startup
connection. PostgreSQL tests create and drop a separate test database.

Back up **both** the PostgreSQL and `batch_data` volumes: metadata and raw
responses are in PostgreSQL; source CSVs and prepared exports are on disk. The
source files are private and no public media URL is configured. Do not remove
these volumes during updates. The current Docker stack is a single-host design;
multi-host deployment needs shared object/file storage for uploads and exports.

Production still requires HTTPS, non-debug settings, correct allowed hosts and
CSRF origins, Discord configuration, backups, and deployment-specific monitoring.
Nginx's upload limit must be increased alongside `BATCH_MAX_UPLOAD_BYTES` if
raising it above 100 MB.

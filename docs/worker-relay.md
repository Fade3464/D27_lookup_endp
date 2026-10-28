# Deploy the lookup Worker relay

The request path is: Django / CSV worker → authenticated Cloudflare Worker →
`https://marstools.co/d27suppressioncheck/ping.php`.

Both campaigns still use one upstream request per number. Public lookup,
authenticated campaign lookup, CSV processing, response validation, audit records,
exports, and staff-controlled safety recovery retain their existing behavior.
The public app endpoint has no application rate limit added by this feature.

## 1. Deploy the Worker first

In Cloudflare **Workers & Pages → d27-lookup-relay → Edit code**, replace the
Hello World module with the entire contents of
[deploy/worker/worker.mjs](../deploy/worker/worker.mjs), then **Deploy**.

In the Worker's **Settings → Variables and Secrets**, ensure **RELAY_TOKEN** is
a **Secret**, with your generated token. Deploy any pending secret changes.
Use the same token in Django below. It must contain 32–256 printable ASCII
characters without spaces; a random 64-character hexadecimal value works.
Do not put it into the Worker source, React environment, Git, or chat.
Cloudflare documents this secret binding workflow in its
[Secrets guide](https://developers.cloudflare.com/workers/configuration/secrets/).

Worker URL:

```text
https://d27-lookup-relay.kokabali78.workers.dev/lookup
```

This is a backend service using Bearer authentication. Leave Cloudflare Access
off for this Worker unless you separately configure service credentials in the
app. No browser CORS configuration is needed. Keep Worker observability off if
you do not need request metadata retained; this module never logs phone numbers
or credentials. The optional [Wrangler config](../deploy/worker/wrangler.jsonc)
also disables observability. The dashboard editor does not consume that config.

An unauthenticated probe must return **401** JSON, not Hello World:

```bash
curl -i 'https://d27-lookup-relay.kokabali78.workers.dev/lookup' \
  -H 'Content-Type: application/json' \
  --data '{"ani":"2345678901"}'
```

Do this before changing the app environment. The probe never reaches the provider.

## 2. Configure the production app

On your server:

```bash
cd /srv/D27_lookup_endp
git pull --ff-only
nano .env
```

Add or update exactly one copy of each key. Replace the placeholder locally with
the same secret you saved in Cloudflare:

```dotenv
LOOKUP_RELAY_URL=https://d27-lookup-relay.kokabali78.workers.dev/lookup
LOOKUP_RELAY_TOKEN=YOUR_EXISTING_RELAY_TOKEN
```

Then:

```bash
chmod 600 .env
d27() { docker compose -f /srv/D27_lookup_endp/compose.production.yaml "$@"; }

# Build and validate settings before stopping running services.
d27 build backend &&
d27 run --rm --no-deps backend python manage.py check &&
d27 stop lookup_worker file_worker alert_worker backend &&
d27 up -d --no-deps --force-recreate backend lookup_worker file_worker alert_worker

d27 ps
d27 logs --tail 60 backend lookup_worker file_worker alert_worker
```

This change needs no database migration, frontend rebuild, or Nginx change.
Recreating containers loads the new environment; `restart` alone does not.
The database and PgBouncer stay running. If startup fails after the stop, correct
the logged error and rerun the `up` command above.

Check the backend configuration without displaying the secret or sending a lookup:

```bash
d27 exec -T backend python manage.py shell -c \
  'from django.conf import settings; print("Relay enabled:", bool(settings.LOOKUP_RELAY_TOKEN)); print("Lookup URL:", settings.LOOKUP_URL)'
```

Expected: `Relay enabled: True` and your Worker `/lookup` URL.
Then submit one known test number through `/lookup-record` and verify its recorded
result before resuming CSV jobs. A Worker-only probe does not verify provider
connectivity; this authenticated end-to-end check is still required after deployment.
Existing global safety pauses require staff recovery as before. Deployment does
not clear them automatically.

## Failure behavior and rollback

Both relay settings blank retain the existing direct provider connection. Only
one set, an invalid URL, or an invalid token format prevents Django startup.
A wrong but well-formed secret produces Worker HTTP 401 and triggers the existing
abnormal-response safety stop for CSV processing. There is no automatic direct
fallback and no Worker retry. HTTP 429, network failures, timeouts, malformed
responses, and oversized responses use the existing app error/safety handling;
they never classify an unresolved number as allowed or bad.

The Worker accepts only `POST /lookup` with an `ani` string of 10 ASCII digits,
bounds requests to 1 KiB and responses to 1 MiB, and times out provider requests
after 18 seconds (the app timeout is 20 seconds). It forwards provider bytes,
status, Content-Type and Retry-After, without wrapping the lookup response.
Both HTTP hops refuse redirects. The relay token is only sent to the configured
HTTPS Worker. It is not stored in lookup request payloads or sent to the provider.
Responses are not cached. Cloudflare documents the relevant
[redirect and cache options](https://developers.cloudflare.com/workers/runtime-apis/request/).

To deliberately return to direct egress, blank **both** relay values and recreate
the four app services using the `up` command above. Do not resume safety-paused
work until staff verifies the selected connection works.

The Worker constructs fresh provider headers rather than copying incoming
authorization, cookies, or client IP headers. Cloudflare can add its own platform
headers, and Worker egress is not a dedicated/static IP guarantee; see
[Cloudflare's subrequest header behavior](https://developers.cloudflare.com/fundamentals/reference/http-headers/#cf-connecting-ip-in-worker-subrequests).
The separate Cloudflare 403 on your app hostname must still be resolved through
its Security Events/rules; routing outgoing lookups does not change incoming WAF
decisions. Worker plan quotas still apply.

## Local verification

```bash
node deploy/worker/worker.test.mjs
.venv/bin/python backend/manage.py test checker batches --settings=config.settings_test
```

Tests mock the provider and credentials; the redirect test uses a local loopback
HTTP server. No live lookup or Discord notification is sent by these tests.

# Update D27 Desk behind Cloudflare

Target: `d27desk.peoplegraph.co`, host checkout `/srv/D27_lookup_endp`.
Nginx stays on the host. Gunicorn remains private on `127.0.0.1:18000`; the
application, PostgreSQL, PgBouncer, workers and frontend build stay containerized.
The full site config is [deploy/nginx/d27-production.conf](../deploy/nginx/d27-production.conf).

## Cloudflare settings

Make these settings specific to this hostname so other Peoplegraph sites are
unaffected:

1. DNS: keep the existing `d27desk` record pointing at this server. Enable
   **Proxied** (orange cloud) after the server update below succeeds. Do not
   change its target to a Docker/container IP.
2. SSL: use **Full (strict)**. If the zone already uses Full (strict), keep it.
   Otherwise add a **Configuration Rule** matching
   `(http.host eq "d27desk.peoplegraph.co")` and set **SSL = Strict**, rather
   than changing encryption modes for unrelated sites. The existing valid
   Let's Encrypt certificate is used between Cloudflare and Nginx.
3. Cache Rules: match `(http.host eq "d27desk.peoplegraph.co")`, set
   **Cache eligibility = Bypass cache**, and **Browser TTL = Respect origin**.
   Make sure no later matching cache rule overrides this bypass. This includes
   private CSV downloads as well as session/admin/API responses.
4. Keep the public lookup free of Cloudflare rate-limit rules. The origin does
   not rate-limit `/api/individual/check/`. Existing login and authenticated
   campaign-check limits are retained. Do not add interactive challenge rules
   to API requests; JSON clients must receive JSON responses.

The Nginx config trusts `CF-Connecting-IP` only from Cloudflare's official
IPv4/IPv6 ranges (verified 28 October 2026). Direct clients cannot override
their identity by setting that header. Keep the ranges current using Cloudflare's
published IP lists. Existing non-Cloudflare domains and host listeners are not
modified by this site config.

Cloudflare has a separate request-body limit, including multipart overhead.
On Free/Pro it is 100 MB, so uploads near the app's 100 MiB ceiling can receive
413 at the edge. Use smaller CSVs or a plan with adequate headroom; increasing
Nginx limits does not raise Cloudflare's limit.

## Complete server update commands

Run as root in the server shell. First get the updated source onto the server
(`git pull --ff-only` below assumes the release has been pushed to your remote).
The existing `.env`, database, and `var/` are retained. The commands create a
consistent database/files backup while app writers are stopped. Stop on errors;
do not continue past a failed build, backup, migration or Nginx validation.

```bash
set -e
cd /srv/D27_lookup_endp
git pull --ff-only

d27() { docker compose -f /srv/D27_lookup_endp/compose.production.yaml "$@"; }

d27 config --quiet
# Verify the existing origin certificate is present before replacing the site.
test -s /etc/letsencrypt/live/d27desk.peoplegraph.co/fullchain.pem
test -s /etc/letsencrypt/live/d27desk.peoplegraph.co/privkey.pem
openssl x509 -in /etc/letsencrypt/live/d27desk.peoplegraph.co/fullchain.pem -noout -dates

d27 build backend frontend_build

umask 077
D27_BACKUP="/srv/d27-backups/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$D27_BACKUP"
cp .env "$D27_BACKUP/environment"
if [ -f /etc/nginx/sites-available/d27 ]; then
    cp -L /etc/nginx/sites-available/d27 "$D27_BACKUP/nginx.conf"
fi

d27 stop lookup_worker file_worker alert_worker backend

d27 exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
    > "$D27_BACKUP/database.dump"
d27 run --rm --no-deps -T --user root --entrypoint tar file_worker \
    -C /app/data -czf - . > "$D27_BACKUP/batches.tar.gz"

d27 run --rm --no-deps init_storage
d27 run --rm --no-deps migrate
d27 run --rm --no-deps frontend_build
d27 up -d

install -m 644 deploy/nginx/d27-production.conf /etc/nginx/sites-available/d27
ln -sfn /etc/nginx/sites-available/d27 /etc/nginx/sites-enabled/d27
nginx -t
systemctl reload nginx

d27 ps -a
d27 exec backend python manage.py check --deploy
d27 exec backend python manage.py shell -c \
    "from django.conf import settings; print('Hosts:', settings.ALLOWED_HOSTS); print('Lookup:', settings.LOOKUP_URL)"
d27 logs --tail 30 backend lookup_worker file_worker alert_worker
```

`security.W021` about HSTS preload is expected; preload is intentionally not
enabled. Backend/database should be healthy, workers running, and init/build/
migrate jobs should exit 0. The lookup URL should print
`https://one.marstools.co/d27supresscheck/ping.php`.

Safety stops are persisted in PostgreSQL. Deployment does not clear them.
After reviewing/fixing the service failure, an authorized staff account resumes
through **Review & Resume All Queues**, optionally selecting failed-row retries.

## Verification and certificate renewal

Enable Cloudflare proxying and the hostname-specific SSL/cache settings above.
Then run:

```bash
curl -I https://d27desk.peoplegraph.co/lookup-record
curl -i https://d27desk.peoplegraph.co/api/history/
curl -I https://d27desk.peoplegraph.co/protected-batch-exports/not-a-file/result.csv
curl -I http://d27desk.peoplegraph.co/lookup-record

tail -n 20 /var/log/nginx/d27.access.log
tail -n 20 /var/log/nginx/d27.error.log
```

Expected statuses: 200, 401, 404, and an HTTPS redirect. Cloudflare may perform
its own redirect before the origin's 308. Proxied responses normally include
`CF-Ray`. Private routes must not show a Cloudflare cache **HIT**. Cache bypass
can appear as **DYNAMIC** or **BYPASS**. The Nginx access log starts with the
visitor IP; `peer=` shows the Cloudflare connecting IP.

Keep the existing Let's Encrypt renewal path and reload hook:

```bash
install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/d27-nginx <<'HOOK'
#!/bin/sh
set -eu
nginx -t
systemctl reload nginx
HOOK
chmod 755 /etc/letsencrypt/renewal-hooks/deploy/d27-nginx
systemctl enable --now certbot.timer
certbot renew --dry-run
```

The Nginx port-80 ACME challenge location remains available. If this domain's
Cloudflare rules challenge or redirect certificate-validation requests in a
way that prevents renewal, exclude `/.well-known/acme-challenge/` from those
rules or use DNS-01 renewal. Do not disable certificate verification.

If the certificate files do not exist, use the bootstrap/certificate steps in
[the main deployment guide](production-deployment.md) before this update.

## Cloudflare returns a branded 403 block page

A Cloudflare page saying **Sorry, you have been blocked** identifies an edge
security rejection. Cache-bypass rules and origin `set_real_ip_from` directives
do not override it. In the zone's Security Events, filter by the response's
`CF-Ray` value, hostname, and UTC timestamp. Inspect the service, action, and
matched rule before changing an exception. A server-side curl request may be
blocked because of its source IP or automation detection even if a browser on
another network works. Do not infer the exact blocking rule from the 403 alone.

Verify the origin independently from the server, preserving TLS verification:

```bash
curl --resolve d27desk.peoplegraph.co:443:127.0.0.1 \
  -I https://d27desk.peoplegraph.co/lookup-record
curl --resolve d27desk.peoplegraph.co:443:127.0.0.1 \
  -i https://d27desk.peoplegraph.co/api/history/
```

Expected statuses are 200 and 401. If those work but public proxied requests
return the branded 403, adjust the specific Cloudflare rule identified in the
event, scoped to the legitimate traffic/hostname. Keep Full (strict) enabled.

The supplied Nginx site opens `/var/log/nginx/d27.access.log` and
`/var/log/nginx/d27.error.log`. If they are missing, verify that the current
site file is installed and Nginx successfully loaded it:

```bash
cd /srv/D27_lookup_endp
install -m 644 deploy/nginx/d27-production.conf /etc/nginx/sites-available/d27
ln -sfn /etc/nginx/sites-available/d27 /etc/nginx/sites-enabled/d27
nginx -t && systemctl reload nginx
nginx -T 2>&1 | grep -nE 'server_name d27desk\.peoplegraph\.co|d27\.(access|error)\.log|real_ip_header CF-Connecting-IP'
```

Do not create empty log files as a substitute for loading the configuration.
Blocked edge requests need not appear in the origin's access log.

## Sources

- [Cloudflare Full (strict)](https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/full-strict/)
- [Hostname-specific SSL Configuration Rules](https://developers.cloudflare.com/rules/configuration-rules/settings/#ssl)
- [Cloudflare visitor IP restoration](https://developers.cloudflare.com/support/troubleshooting/restoring-visitor-ips/restoring-original-visitor-ips/)
- [IPv4 ranges](https://www.cloudflare.com/ips-v4) and [IPv6 ranges](https://www.cloudflare.com/ips-v6)
- [Cache bypass settings](https://developers.cloudflare.com/cache/how-to/cache-rules/settings/#cache-eligibility)
- [Cloudflare upload limits](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/4xx-client-error/error-413/)

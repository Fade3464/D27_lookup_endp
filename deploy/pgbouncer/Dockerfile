FROM alpine:3.23
RUN apk add --no-cache pgbouncer && mkdir -p /run/pgbouncer && chown nobody:nobody /run/pgbouncer
COPY entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh
USER nobody
ENTRYPOINT ["/entrypoint.sh"]

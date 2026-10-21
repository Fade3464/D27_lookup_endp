#!/bin/sh
set -eu
umask 077
# Keep config interpolation safe and allow generated URL-safe credentials.
case "$DB_USER:$DB_NAME" in *[!a-zA-Z0-9_:]*) echo 'DB_USER and DB_NAME must contain only letters, digits or underscores.' >&2; exit 1;; esac
case "$DB_PASSWORD" in ''|*[!a-zA-Z0-9_-]*) echo 'DB_PASSWORD must be a nonempty URL-safe token.' >&2; exit 1;; esac
printf '"%s" "%s"\n' "$DB_USER" "$DB_PASSWORD" > /run/pgbouncer/users.txt
cat > /run/pgbouncer/pgbouncer.ini <<EOF
[databases]
$DB_NAME = host=postgres port=5432 dbname=$DB_NAME
[pgbouncer]
listen_addr = 0.0.0.0
listen_port = 6432
unix_socket_dir = /run/pgbouncer
auth_type = scram-sha-256
auth_file = /run/pgbouncer/users.txt
pool_mode = transaction
max_client_conn = 200
default_pool_size = 20
reserve_pool_size = 5
server_reset_query = DISCARD ALL
ignore_startup_parameters = extra_float_digits
EOF
exec pgbouncer /run/pgbouncer/pgbouncer.ini

#!/bin/sh
# Run inside the Node build container; only /output is published to host Nginx.
set -eu
umask 022
mkdir -p /output
for asset in /app/dist/* /app/dist/.[!.]* /app/dist/..?*; do
    [ -e "$asset" ] || continue
    [ "${asset##*/}" != index.html ] || continue
    cp -R "$asset" /output/
done
# Keep old hashed assets available to open browser tabs. Publish HTML last.
cp /app/dist/index.html /output/.index.html.next
chmod 644 /output/.index.html.next
mv /output/.index.html.next /output/index.html

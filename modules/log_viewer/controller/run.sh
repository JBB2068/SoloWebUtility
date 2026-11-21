#!/bin/sh
# Starts the read-only flight log viewer on the Solo controller and starts it again if it stops.
# Installed by the Solo Web Utility (modules/log_viewer/controller_install.py). One copy only.
ROOT=/log/logviewer
PIDFILE=/run/logviewer.pid
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    exit 0
fi
echo $$ > "$PIDFILE"
while true; do
    if [ -f "$ROOT/server.log" ] && [ "$(wc -c < "$ROOT/server.log")" -gt 262144 ]; then
        mv "$ROOT/server.log" "$ROOT/server.log.1"
    fi
    nice -n 19 python "$ROOT/log_server.py" --root "$ROOT" >> "$ROOT/server.log" 2>&1
    sleep 5
done

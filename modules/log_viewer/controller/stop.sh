#!/bin/sh
# Stops the flight log viewer.
PIDFILE=/run/logviewer.pid
if [ -f "$PIDFILE" ]; then
    kill "$(cat "$PIDFILE")" 2>/dev/null
fi
for p in $(ps | grep '[l]og_server.py' | awk '{print $1}'); do
    kill "$p" 2>/dev/null
done
rm -f "$PIDFILE"
echo stopped

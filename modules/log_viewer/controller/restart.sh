#!/bin/sh
# Stops the flight log viewer (if running) and starts it again.
ROOT=/log/logviewer
PIDFILE=/run/logviewer.pid
if [ -f "$PIDFILE" ]; then
    kill "$(cat "$PIDFILE")" 2>/dev/null
fi
for p in $(ps | grep '[l]og_server.py' | awk '{print $1}'); do
    kill "$p" 2>/dev/null
done
sleep 1
rm -f "$PIDFILE"
nohup "$ROOT/run.sh" >/dev/null 2>&1 </dev/null &
sleep 1
echo started

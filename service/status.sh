#!/bin/bash
# Show whether the Mac Thread router service is running and what it is doing.
LABEL="local.macthreadrouter"

echo "Service:"
if out="$(launchctl print "system/$LABEL" 2>/dev/null)"; then
  echo "$out" | grep -E '^[[:space:]]*(state|pid|last exit code|runs) =' | sed 's/^[[:space:]]*/  /'
else
  echo "  not installed or not loaded"
fi

echo "Apple threadradiod:"
pid="$(pgrep -x threadradiod)" && ps -o pid=,state=,comm= -p "$pid" | sed 's/^/  /' \
  || echo "  not running"
echo "  (state T = suppressed, which is expected while the service runs)"

echo "Recent log:"
tail -n 15 /Library/Logs/MacThreadRouter.log 2>/dev/null | sed 's/^/  /' || echo "  no log yet"

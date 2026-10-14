#!/usr/bin/env bash
# The stream's safety net, exercised against fake games.
#
# It was installed and left untested for a day: its log was empty, which reads
# like "nothing has gone wrong" and equally means "nobody knows if it works".
# The live SIGSTOP test proved the no-answer branch; this covers the rest,
# including the two things that make it dangerous rather than merely useless --
# choosing between two games, and how it ends one.
#
# Run: bash tests/test_watchdog.sh   (takes about a minute)
set -u
HERE=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
PORT=8798
PASS=0; FAIL=0

ok()   { PASS=$((PASS+1)); echo "  PASS  $1"; }
bad()  { FAIL=$((FAIL+1)); echo "  FAIL  $1"; }
check(){ if [ "$2" = "yes" ]; then ok "$1"; else bad "$1"; fi; }

cleanup() {
    [ -n "${HEALTH_PID:-}" ] && kill "$HEALTH_PID" 2>/dev/null
    for f in "$WORK"/*.pid; do [ -f "$f" ] && kill -9 "$(cat "$f")" 2>/dev/null; done
    pkill -P $$ 2>/dev/null
    rm -rf "$WORK"
}
trap cleanup EXIT

# --- a fake game: matches the watchdog's process pattern ------------------
mkdir -p "$WORK/fake"
cat > "$WORK/fake/flybrainfake.py" <<'EOF'
import signal, sys, time
# argv[2] says whether this one can still save when asked
responsive = len(sys.argv) > 2 and sys.argv[2] == "responsive"
if responsive:
    signal.signal(signal.SIGUSR1, lambda *a: sys.exit(75))
else:
    signal.signal(signal.SIGUSR1, signal.SIG_IGN)   # wedged: cannot answer
while True:
    time.sleep(0.2)
EOF

start_fake() {   # $1 = responsive|wedged
    :
    python3 "$WORK/fake/flybrainfake.py" play "$1" >/dev/null 2>&1 &
    echo $! > "$WORK/game.pid"
    sleep 1
}

# --- a health endpoint we control ----------------------------------------
cat > "$WORK/health.py" <<EOF
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        try:
            body = open("$WORK/frames").read().encode()
        except OSError:
            self.send_response(500); self.end_headers(); return
        self.send_response(200); self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)
HTTPServer(("127.0.0.1", $PORT), H).serve_forever()
EOF
echo "ok frames=1" > "$WORK/frames"
python3 "$WORK/health.py" & HEALTH_PID=$!
sleep 1.5

# --- the real script, pointed at our fakes --------------------------------
sed -e "s|^HEALTH=.*|HEALTH=http://127.0.0.1:$PORT/health|" \
    -e "s|^STALL_S=.*|STALL_S=\${STALL_S:-4}|" \
    -e "s|^POLL_S=.*|POLL_S=\${POLL_S:-1}|" \
    -e "s|^GRACE_S=.*|GRACE_S=\${GRACE_S:-4}|" \
    -e "s|^BOOT_GRACE_S=.*|BOOT_GRACE_S=\${BOOT_GRACE_S:-8}|" \
    -e "s|~/flybrain/play/watchdog.log|$WORK/wd.log|" \
    -e "s|flybrain\\\\.py play|flybrainfake\\\\.py play|" \
    "${WATCHDOG:-$HERE/play/watchdog.sh}" > "$WORK/wd.sh"
bash -n "$WORK/wd.sh" || { echo "the patched copy does not parse"; exit 1; }

run_wd() { : > "$WORK/wd.log"; timeout "$1" bash "$WORK/wd.sh" >/dev/null 2>&1; }

echo "1. a healthy game is left alone"
start_fake responsive
( for i in $(seq 2 40); do echo "ok frames=$i" > "$WORK/frames"; sleep 0.5; done ) &
TICKER=$!
run_wd 8
kill "$TICKER" 2>/dev/null
alive=$(kill -0 "$(cat "$WORK/game.pid")" 2>/dev/null && echo yes || echo no)
check "a game whose frames advance is not touched" "$alive"
check "and nothing is logged" "$([ ! -s "$WORK/wd.log" ] && echo yes || echo no)"
kill -9 "$(cat "$WORK/game.pid")" 2>/dev/null

echo "2. a wedged game that CAN still save is asked, not forced"
start_fake responsive
echo "ok frames=999" > "$WORK/frames"          # counter frozen
run_wd 12
log=$(cat "$WORK/wd.log")
check "it notices the stuck counter" "$(echo "$log" | grep -q 'frame counter stuck' && echo yes || echo no)"
check "it asks the game to save first" "$(echo "$log" | grep -q 'asking pid .* to save' && echo yes || echo no)"
check "the game saved and left by itself" "$(echo "$log" | grep -q 'saved and exited on its own' && echo yes || echo no)"
check "so it was never forced out" "$(echo "$log" | grep -q 'forcing it out' && echo no || echo yes)"

echo "3. a game too wedged to answer is forced out"
start_fake wedged
echo "ok frames=999" > "$WORK/frames"
run_wd 16
log=$(cat "$WORK/wd.log")
check "it still asks first" "$(echo "$log" | grep -q 'asking pid .* to save' && echo yes || echo no)"
check "then forces it out" "$(echo "$log" | grep -q 'forcing it out' && echo yes || echo no)"
check "and says progress was lost" "$(echo "$log" | grep -q 'progress since the last autosave is lost' && echo yes || echo no)"
gone=$(kill -0 "$(cat "$WORK/game.pid")" 2>/dev/null && echo no || echo yes)
check "the process really is gone" "$gone"

echo "4. a game that stops answering at all"
start_fake wedged
kill "$HEALTH_PID" 2>/dev/null; sleep 1        # health endpoint down
run_wd 16
log=$(cat "$WORK/wd.log")
check "no answer is treated as hung" "$(echo "$log" | grep -q 'no answer from the game' && echo yes || echo no)"
kill -9 "$(cat "$WORK/game.pid")" 2>/dev/null
python3 "$WORK/health.py" & HEALTH_PID=$!
sleep 1.5

echo "5. two games: refuse to choose"
start_fake responsive; mv "$WORK/game.pid" "$WORK/game1.pid"
start_fake responsive; mv "$WORK/game.pid" "$WORK/game2.pid"
echo "ok frames=999" > "$WORK/frames"
run_wd 10
log=$(cat "$WORK/wd.log")
check "it refuses to pick between two games" "$(echo "$log" | grep -q 'refusing to choose' && echo yes || echo no)"
a=$(kill -0 "$(cat "$WORK/game1.pid")" 2>/dev/null && echo yes || echo no)
b=$(kill -0 "$(cat "$WORK/game2.pid")" 2>/dev/null && echo yes || echo no)
check "and kills neither of them" "$([ "$a" = yes ] && [ "$b" = yes ] && echo yes || echo no)"
kill -9 "$(cat "$WORK/game1.pid")" "$(cat "$WORK/game2.pid")" 2>/dev/null

echo "6. no game at all is none of its business"
rm -f "$WORK"/*.pid
echo "ok frames=999" > "$WORK/frames"
run_wd 8
check "it does nothing when no game is running" "$([ ! -s "$WORK/wd.log" ] && echo yes || echo no)"

echo "7. the window still publishing but the EMULATOR hung"
start_fake responsive
( for i in $(seq 2 40); do printf 'ok frames=%s\nphase=playing title=auto emu=777\n' "$i" > "$WORK/frames"; sleep 0.5; done ) &
TICKER=$!
run_wd 12
kill "$TICKER" 2>/dev/null
log=$(cat "$WORK/wd.log")
check "a stuck emulator is caught even though frames= rises" "$(echo "$log" | grep -q 'frame counter stuck at emu777' && echo yes || echo no)"
kill -9 "$(cat "$WORK/game.pid")" 2>/dev/null

echo "8. still loading: the emulator sits still and that is fine"
start_fake responsive
( for i in $(seq 2 40); do printf 'ok frames=%s\nphase=loading title=auto emu=0\n' "$i" > "$WORK/frames"; sleep 0.5; done ) &
TICKER=$!
run_wd 8
kill "$TICKER" 2>/dev/null
alive=$(kill -0 "$(cat "$WORK/game.pid")" 2>/dev/null && echo yes || echo no)
check "a game still loading is not killed for a still emulator" "$alive"
kill -9 "$(cat "$WORK/game.pid")" 2>/dev/null

echo "9. playing, emulator advancing: left alone"
start_fake responsive
( for i in $(seq 2 40); do printf 'ok frames=5\nphase=playing title=auto emu=%s\n' "$i" > "$WORK/frames"; sleep 0.5; done ) &
TICKER=$!
run_wd 8
kill "$TICKER" 2>/dev/null
alive=$(kill -0 "$(cat "$WORK/game.pid")" 2>/dev/null && echo yes || echo no)
check "an advancing emulator is left alone" "$alive"
kill -9 "$(cat "$WORK/game.pid")" 2>/dev/null

echo "10. a new game still loading (never answered) gets the boot grace"
start_fake responsive
kill "$HEALTH_PID" 2>/dev/null; sleep 1
run_wd 7
alive=$(kill -0 "$(cat "$WORK/game.pid")" 2>/dev/null && echo yes || echo no)
check "not killed inside the boot grace (STALL_S=4, grace 8)" "$alive"
kill -9 "$(cat "$WORK/game.pid")" 2>/dev/null
python3 "$WORK/health.py" & HEALTH_PID=$!
sleep 1.5

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] || exit 1

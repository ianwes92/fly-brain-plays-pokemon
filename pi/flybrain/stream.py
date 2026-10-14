"""The play window, served over the network for OBS on another machine.

Why not screen-share the Pi: the Pi 5 has no hardware video encoder and the
brain already uses every core, so encoding a real video stream here would slow
the fly. JPEG snapshots of the window are cheap by comparison (a few ms each),
and OBS on the streaming PC does the heavy encoding. It also captures exactly
the program's window at its full size, whatever the Pi's own screen can show.

  http://<pi>:<port>/             a page holding the live window -- use this
                                  as an OBS Browser Source, 800 x 450
  http://<pi>:<port>/stream.mjpg  the raw MJPEG stream
  http://<pi>:<port>/frame.jpg    the latest single frame
  http://<pi>:<port>/health       'ok' plus frame count, for checking
  http://<pi>:<port>/audio.raw    the game's sound, raw 16-bit stereo at
                                  AUDIO_RATE, as fast as the game makes it --
                                  for the relay (relay.py), which paces it to
                                  real time; not meant for OBS directly

View-only from the network: nothing sent here from another machine can touch
the game. The single exception is POST /test-cheer, which rehearses a Twitch
cheer in the live game (flybrain test-cheer) and is refused unless it comes
from the Pi itself -- the broadcaster cannot cheer in their own channel, so
this is how the Pi's half of the Bits path is checked. It listens on the local
network, so anyone on that network can watch -- which is the point, but worth
knowing.
"""
import io
import json
import threading
import time

import numpy as np
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BOUNDARY = "flyframe"
AUDIO_RATE = 48000             # the emulator makes ~96 kHz; halved on the way in

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>flybrain</title>
<style>html,body{{margin:0;background:#000;overflow:hidden}}
img{{display:block;width:{w}px;height:{h}px}}</style></head>
<body><img src="stream.mjpg" alt="flybrain"></body></html>"""


class FrameServer:
    def __init__(self, port=8765, host="0.0.0.0", size=(800, 500), log=print):
        self.port, self.host, self.size, self.log = port, host, size, log
        self._jpeg = None
        self._count = 0
        self._jpeg_pos = 0     # sound position of the frame in hand
        self._audio_total = 0  # samples of sound made since the game started
        self.status = None     # optional callable -> a line for /health
        self.events = None     # optional callable(since) -> numbered news
        self.live = None       # optional callable -> dict, for the panel's live view
        self.overlay = None    # optional callable -> dict, everything the overlay draws
        # The bare GBA frame for the OBS-side HUD (2026-10-09): lossless PNG
        # (pixel art scaled 4.8x must not carry JPEG smear), encoded only while
        # somebody is watching /game.mjpg or asked /game.png lately.
        self._game_png = None
        self._game_count = 0
        self._game_watchers = 0
        self._game_asked = 0.0
        self._gcond = threading.Condition()
        self._cond = threading.Condition()
        self._running = True
        self.inject = None          # set by the window when Twitch is on
        self.control = None         # callable(dict) -> dict, the control panel
        self.clip = None            # callable() -> dict, a clip of the last 30 s (ig_story)
        self._audio = []            # pending PCM chunks (bytes), newest last
        self._audio_seq = 0
        self._acond = threading.Condition()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):          # keep the terminal quiet
                pass

            def do_GET(self):
                path = self.path.split("?")[0]
                if path in ("/", "/index.html"):
                    body = PAGE.format(w=server.size[0], h=server.size[1]).encode()
                    self._send(200, "text/html; charset=utf-8", body)
                elif path == "/frame.jpg":
                    with server._cond:
                        jpeg = server._jpeg
                    if jpeg is None:
                        self._send(503, "text/plain", b"no frame yet")
                    else:
                        self._send(200, "image/jpeg", jpeg)
                elif path == "/health":
                    # `status` lets the game add a line of its own (what chat
                    # is doing), so the stream can be checked from the other
                    # machine without reading pixels.
                    extra = ""
                    try:
                        if server.status is not None:
                            extra = str(server.status())
                    except Exception as e:                  # never break /health
                        extra = f"status failed: {type(e).__name__}"
                    self._send(200, "text/plain",
                               f"ok frames={server._count}\n{extra}\n".encode())
                elif path in ("/overlay", "/game.png"):
                    if path == "/game.png":
                        server._game_asked = time.time()
                        with server._gcond:
                            png = server._game_png
                        if png is None:
                            return self._send(503, "text/plain", b"no game frame yet")
                        return self._send(200, "image/png", png)
                    if server.overlay is None:
                        return self._send(503, "application/json", b'{"why": "not playing yet"}')
                    try:
                        body = json.dumps(server.overlay()).encode()
                    except Exception as e:
                        body = json.dumps({"why": f"overlay failed: {type(e).__name__}"}).encode()
                    self._send(200, "application/json", body)
                elif path == "/game.mjpg":
                    self._game_stream()
                elif path == "/live":
                    # The panel's live view (obsfly polls ~2 Hz through the
                    # relay's /panel/live): small, in-memory, no API calls.
                    if server.live is None:
                        return self._send(503, "application/json", b'{"why": "not playing yet"}')
                    try:
                        body = json.dumps(server.live()).encode()
                    except Exception as e:                  # never break the server
                        body = json.dumps({"why": f"live failed: {type(e).__name__}"}).encode()
                    self._send(200, "application/json", body)
                elif path == "/events":
                    # What the fly has done, numbered, for the Discord poster
                    # and anything after it. Read-only and cheap; the ids are
                    # what let a poller resume without repeating itself.
                    if server.events is None:
                        return self._send(503, "application/json",
                                          b'{"events": [], "why": "not playing yet"}')
                    q = urllib.parse.parse_qs(
                        urllib.parse.urlparse(self.path).query)
                    try:
                        rows = server.events(q.get("since", ["0"])[0])
                    except Exception as e:                  # never break the feed
                        return self._send(500, "application/json",
                                          json.dumps({"events": [],
                                                      "why": type(e).__name__}).encode())
                    self._send(200, "application/json",
                               json.dumps({"events": rows}).encode())
                elif path == "/stream.mjpg":
                    self._stream()
                elif path == "/audio.raw":
                    self._audio_stream()
                else:
                    self._send(404, "text/plain", b"not found")

            def do_POST(self):
                if self.path.split("?")[0] == "/clip":
                    # A clean clip of the last 30 s, rendered now, for the
                    # Instagram Story fallback (play/ig_story.py). Pi only.
                    if self.client_address[0] not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
                        return self._send(403, "text/plain", b"from the Pi itself only")
                    if server.clip is None:
                        return self._send(409, "application/json", b'{"ok": false, "why": "no recorder"}')
                    try:
                        out = server.clip()
                    except Exception as e:
                        out = {"ok": False, "why": f"{type(e).__name__}: {e}"}
                    return self._send(200, "application/json", json.dumps(out).encode())
                if self.path.split("?")[0] == "/panel/control":
                    # From the relay on this Pi only; the relay does the
                    # authentication (see relay.py /panel/control).
                    if self.client_address[0] not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
                        return self._send(403, "text/plain", b"through the relay only")
                    if server.control is None:
                        return self._send(409, "application/json",
                                          b'{"ok": false, "why": "chat control is not on in this game"}')
                    try:
                        n = int(self.headers.get("Content-Length", 0))
                        out = server.control(json.loads(self.rfile.read(n) or b"{}"))
                    except Exception as e:
                        out = {"ok": False, "why": f"{type(e).__name__}: {e}"}
                    return self._send(200, "application/json", json.dumps(out).encode())
                if self.path.split("?")[0] != "/test-cheer":
                    return self._send(404, "text/plain", b"not found")
                if self.client_address[0] not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
                    return self._send(403, "text/plain", b"rehearsals only from the Pi itself")
                if server.inject is None:
                    return self._send(409, "text/plain", b"Twitch is not on in this game")
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    req = json.loads(self.rfile.read(n) or b"{}")
                    ev = server.inject(req)
                except Exception as e:                      # a bad request, not a crash
                    return self._send(400, "text/plain", f"bad rehearsal: {e}".encode())
                self._send(200, "application/json", json.dumps({"event": ev}).encode())

            def _send(self, code, ctype, body):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _audio_stream(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("X-Sample-Rate", str(AUDIO_RATE))
                self.send_header("X-Channels", "2")
                self.send_header("Cache-Control", "no-store")
                with server._acond:
                    seen = server._audio_seq
                    start = server._audio_total
                self.send_header("X-Audio-Pos", str(start))
                self.end_headers()
                try:
                    while server._running:
                        with server._acond:
                            server._acond.wait_for(
                                lambda: server._audio_seq != seen or not server._running, timeout=2.0)
                            if not server._running:
                                break
                            # Only chunks newer than what this listener has had.
                            fresh = server._audio_seq - seen
                            chunks = server._audio[-fresh:] if fresh <= len(server._audio) else list(server._audio)
                            seen = server._audio_seq
                        for c in chunks:
                            self.wfile.write(c)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

            def _game_stream(self):
                self.send_response(200)
                self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                seen = -1
                with server._gcond:
                    server._game_watchers += 1
                try:
                    while server._running:
                        with server._gcond:
                            server._gcond.wait_for(
                                lambda: server._game_count != seen or not server._running, timeout=2.0)
                            if not server._running:
                                break
                            png, seen = server._game_png, server._game_count
                        if png is None:
                            continue
                        self.wfile.write(f"--{BOUNDARY}\r\nContent-Type: image/png\r\n"
                                         f"Content-Length: {len(png)}\r\n\r\n".encode())
                        self.wfile.write(png)
                        self.wfile.write(b"\r\n")
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    with server._gcond:
                        server._game_watchers -= 1

            def _stream(self):
                self.send_response(200)
                self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                seen = -1
                try:
                    while server._running:
                        with server._cond:
                            server._cond.wait_for(
                                lambda: server._count != seen or not server._running, timeout=2.0)
                            if not server._running:
                                break
                            jpeg, seen = server._jpeg, server._count
                            pos = server._jpeg_pos
                        if jpeg is None:
                            continue
                        self.wfile.write(f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                                         f"X-Audio-Pos: {pos}\r\n"
                                         f"Content-Length: {len(jpeg)}\r\n\r\n".encode())
                        self.wfile.write(jpeg)
                        self.wfile.write(b"\r\n")
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass                          # a viewer went away; that is fine

        self._httpd = ThreadingHTTPServer((host, port), Handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def start(self):
        self._thread.start()
        self.log(f"streaming the window at http://<this pi>:{self.port}/ (OBS Browser Source)")
        return self

    def publish_game(self, shot):
        """The bare GBA frame (H x W x 3 uint8) for /game.mjpg and /game.png.
        Skipped unless somebody is watching or asked for a still lately."""
        if self._game_watchers <= 0 and time.time() - self._game_asked > 10:
            return
        from PIL import Image
        buf = io.BytesIO()
        Image.fromarray(np.ascontiguousarray(shot)).save(buf, "PNG", compress_level=1)
        with self._gcond:
            self._game_png = buf.getvalue()
            self._game_count += 1
            self._gcond.notify_all()

    def publish(self, surface):
        """Encode the window as JPEG and hand it to every viewer. Call once per
        drawn frame from the window's own thread."""
        # Encoded with PIL, not pygame.image.save: pygame 2.1.2 leaks the whole
        # image on every save to a file object (measured: 299 KB lost per
        # 298 KB frame), which at 15 fps filled the Pi's 16 GB in ~3.5 hours
        # and the out-of-memory killer took the game down mid-stream. PIL:
        # no leak, ~6 ms a frame.
        import numpy as np
        import pygame
        from PIL import Image
        # A view on the window's pixels, not a copy (a copy tripled the cost);
        # the surface stays locked only until the view is dropped.
        view = pygame.surfarray.pixels3d(surface)
        try:
            img = Image.fromarray(np.ascontiguousarray(view.swapaxes(0, 1)))
        finally:
            del view
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        # The sound position this picture belongs to. The relay releases the
        # frame when it is sending that sample, which is exact sync -- far
        # better than both ends guessing at a delay that drifts by a second.
        pos = self._audio_total
        with self._cond:
            self._jpeg = buf.getvalue()
            self._jpeg_pos = pos
            self._count += 1
            self._cond.notify_all()

    def push_audio(self, samples):
        """Hand over one game frame's sound: (n, 2) int16 at the emulator's
        ~96 kHz. Halved to AUDIO_RATE by averaging pairs. Safe to call from
        the game thread; keeps only the last few seconds for late listeners."""
        import numpy as np
        n = len(samples) // 2 * 2
        if n == 0:
            return
        half = samples[:n].reshape(-1, 2, 2).astype(np.int32).mean(axis=1).astype(np.int16)
        with self._acond:
            self._audio.append(half.tobytes())
            if len(self._audio) > 300:          # ~5 s of game frames
                del self._audio[:len(self._audio) - 300]
            self._audio_total += len(half)
            self._audio_seq += 1
            self._acond.notify_all()

    def stop(self):
        self._running = False
        with self._cond:
            self._cond.notify_all()
        with self._acond:
            self._acond.notify_all()
        self._httpd.shutdown()
        self._httpd.server_close()

"""Minimal obs-websocket v5 client (stdlib only)."""
import socket, ssl, os, base64, hashlib, json, struct, itertools

class OBS:
    def __init__(self, host="127.0.0.1", port=4455, password=None, timeout=10):
        self.ids = itertools.count(1)
        self.sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
               f"Sec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        assert b"101" in buf.split(b"\r\n")[0], buf[:200]
        self.rest = buf.split(b"\r\n\r\n", 1)[1]
        hello = self.recv()
        auth = hello["d"].get("authentication")
        ident = {"rpcVersion": 1}
        if auth:
            secret = base64.b64encode(hashlib.sha256((password + auth["salt"]).encode()).digest()).decode()
            ident["authentication"] = base64.b64encode(
                hashlib.sha256((secret + auth["challenge"]).encode()).digest()).decode()
        self.send({"op": 1, "d": ident})
        r = self.recv()
        assert r["op"] == 2, r

    def _read(self, n):
        while len(self.rest) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("closed")
            self.rest += chunk
        out, self.rest = self.rest[:n], self.rest[n:]
        return out

    def recv(self):
        b1, b2 = self._read(2)
        ln = b2 & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", self._read(2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", self._read(8))[0]
        return json.loads(self._read(ln).decode())

    def send(self, obj):
        data = json.dumps(obj).encode()
        hdr = bytearray([0x81])
        n = len(data)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126); hdr += struct.pack(">H", n)
        else:
            hdr.append(0x80 | 127); hdr += struct.pack(">Q", n)
        mask = os.urandom(4)
        hdr += mask
        self.sock.sendall(bytes(hdr) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def call(self, rtype, data=None):
        rid = str(next(self.ids))
        self.send({"op": 6, "d": {"requestType": rtype, "requestId": rid, "requestData": data or {}}})
        while True:
            m = self.recv()
            if m["op"] == 7 and m["d"]["requestId"] == rid:
                st = m["d"]["requestStatus"]
                if not st["result"]:
                    raise RuntimeError(f"{rtype}: {st.get('code')} {st.get('comment')}")
                return m["d"].get("responseData") or {}

def connect():
    cfg = json.load(open(os.path.expandvars(
        r"%APPDATA%\obs-studio\plugin_config\obs-websocket\config.json"), encoding="utf-8"))
    return OBS(password=cfg["server_password"])

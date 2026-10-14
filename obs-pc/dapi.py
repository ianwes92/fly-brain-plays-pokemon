import urllib.request, json, pathlib
TOK = pathlib.Path(__file__).with_name('dbot.txt').read_text().strip()
BASE = 'https://discord.com/api/v10'
H = {'Authorization': 'Bot ' + TOK, 'User-Agent': 'flybrain-discord/1.0', 'Content-Type': 'application/json'}
def call(path, method='GET', body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=H, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=20)
        raw = r.read()
        return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'{method} {path} -> {e.code} {e.read().decode()[:300]}') from None

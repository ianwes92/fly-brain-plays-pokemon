#!/usr/bin/env python3
"""Fetch the FlyWire FAFB v783 connectome files from Zenodo record 17030629.

Zenodo drops long connection, so the big connectivity file is pulled in
ranged chunks with per-chunk retries and verified against the record's md5.
Re-running is cheap: complete, checksum-matching files are skipped and a
partial download resumes where it stopped.

Usage:
    ./fetch_data.py              # fetch anything missing into ./data
    ./fetch_data.py --recheck    # re-verify checksums of files already there
"""
import fcntl
import hashlib
import http.client
import os
import sys
import time
import urllib.error
import urllib.request

RECORD = "17030629"
API = f"https://zenodo.org/api/records/{RECORD}"
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

CHUNK = 4 << 20       # 4 MiB per ranged request
TRIES = 8             # attempts per chunk
UA = {"User-Agent": "flybrain/0.1 (connectome fetch)"}


def _get(url, headers=None, timeout=120):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout)


def manifest():
    """[(filename, size, md5), ...] straight from the Zenodo record."""
    import json
    with _get(API) as r:
        rec = json.load(r)
    out = []
    for f in rec["files"]:
        digest = f.get("checksum", "")
        out.append((f["key"], f["size"], digest.split(":", 1)[-1]))
    return out


def md5sum(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def fetch(name, size, want_md5):
    final = os.path.join(DATA, name)
    part = final + ".part"

    if os.path.exists(final) and os.path.getsize(final) == size:
        if md5sum(final) == want_md5:
            print(f"  {name}: already complete")
            return True
        print(f"  {name}: checksum mismatch, refetching")
        os.remove(final)

    url = f"{API}/files/{name}/content"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    if have > size:
        os.remove(part)
        have = 0
    if have:
        print(f"  {name}: resuming at {human(have)}")

    started = time.time()
    with open(part, "ab") as out:
        # Two fetchers appending to one .part silently corrupts it, and the
        # result still has the right length. Take an exclusive lock instead.
        try:
            fcntl.flock(out.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(f"  {name}: another fetch already holds this file")
            return False
        while have < size:
            end = min(have + CHUNK, size) - 1
            block = b""
            for attempt in range(TRIES):
                try:
                    with _get(url, {"Range": f"bytes={have}-{end}"}) as r:
                        block = r.read()
                    break
                except http.client.IncompleteRead as exc:
                    # Zenodo cuts the response short constantly. Whatever
                    # arrived is still good bytes -- bank it and re-range
                    # from the new offset instead of retrying the chunk.
                    block = exc.partial
                    if block:
                        break
                except (urllib.error.URLError, http.client.HTTPException,
                        OSError, TimeoutError) as exc:
                    if attempt == TRIES - 1:
                        print(f"\n  {name}: giving up at {human(have)} ({exc})")
                        return False
                    time.sleep(min(2 ** attempt, 20))
            if not block:
                print(f"\n  {name}: no progress at {human(have)}")
                return False
            out.write(block)
            out.flush()
            have += len(block)
            assert out.tell() == have, "concurrent writer detected"
            rate = have / max(time.time() - started, 1e-6)
            pct = 100 * have / size
            sys.stdout.write(
                f"\r  {name}: {pct:5.1f}%  {human(have)}/{human(size)}  {human(rate)}/s   ")
            sys.stdout.flush()
    print()

    got = md5sum(part)
    if got != want_md5:
        print(f"  {name}: MD5 MISMATCH (got {got}, want {want_md5})")
        return False
    os.replace(part, final)
    print(f"  {name}: verified")
    return True


def main():
    os.makedirs(DATA, exist_ok=True)
    recheck = "--recheck" in sys.argv

    print(f"FlyWire v783 -> {DATA}")
    files = manifest()
    # Small annotation files first so a slow connectivity pull never blocks them.
    files.sort(key=lambda f: f[1])

    ok = True
    for name, size, digest in files:
        final = os.path.join(DATA, name)
        if recheck and os.path.exists(final):
            state = "ok" if md5sum(final) == digest else "BAD"
            print(f"  {name}: {state}")
            continue
        ok &= fetch(name, size, digest)

    if not ok:
        print("\nSome files are incomplete. Re-run to resume.")
        return 1
    print("\nAll files present and verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

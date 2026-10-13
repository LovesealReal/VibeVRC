import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request

from . import __version__

REPO = "LovesealReal/VibeVRC"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE = f"https://github.com/{REPO}/releases/latest"
ASSET = "VibeVRC.exe"


def parse(v):
    try:
        parts = [int(p) for p in str(v).strip().lstrip("vV").split(".")]
    except ValueError:
        return None
    return tuple((parts + [0, 0, 0])[:3])


def get(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": "VibeVRC/" + __version__,
                                               "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def check(current=__version__):
    with get(API, 15) as r:
        rel = json.load(r)
    new, now = parse(rel.get("tag_name", "")), parse(current)
    if not new or not now or new <= now or rel.get("draft") or rel.get("prerelease"):
        return None
    asset = next((a for a in rel.get("assets", []) if a.get("name") == ASSET), None)
    digest = (asset or {}).get("digest") or ""
    if not asset or not digest.startswith("sha256:"):
        return None
    return {"version": ".".join(map(str, new)).removesuffix(".0"), "url": asset["browser_download_url"],
            "sha256": digest[7:].lower()}


def exe():
    return sys.executable if getattr(sys, "frozen", False) else None


def can_install():
    path = exe()
    if not path:
        return False
    test = path + ".write-test"
    try:
        with open(test, "w") as f:
            f.write("ok")
        os.remove(test)
        return True
    except OSError:
        return False


def download(info, dest):
    h = hashlib.sha256()
    try:
        with get(info["url"], 30) as r, open(dest, "wb") as f:
            while chunk := r.read(1 << 16):
                h.update(chunk)
                f.write(chunk)
    except BaseException:
        remove(dest)
        raise
    if h.hexdigest() != info["sha256"]:
        remove(dest)
        raise ValueError("the download didn't match the release")


def install(info, path=None):
    path = path or exe()
    new, old = path + ".new", path + ".old"
    download(info, new)
    remove(old)
    os.replace(path, old)
    try:
        os.replace(new, path)
    except OSError:
        os.replace(old, path)
        remove(new)
        raise
    return path


def relaunch(path):
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    subprocess.Popen([path, "--updated"], env=env, close_fds=True,
                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)


def remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def cleanup(tries=40):
    path = exe()
    for _ in range(tries if path else 0):
        remove(path + ".old")
        remove(path + ".new")
        if not os.path.exists(path + ".old"):
            return
        time.sleep(0.5)

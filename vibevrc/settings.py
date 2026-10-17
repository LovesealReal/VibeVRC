import dataclasses
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

from .relay import format_code, normalize_code


@dataclass
class Config:
    osc_port: int = 0
    send_ip: str = "127.0.0.1"
    send_port: int = 9000
    relay_host: str = "broker.emqx.io"
    relay_port: int = 8883
    relay_tls: bool = True
    name: str = ""
    targets: list = field(default_factory=list)
    share: bool = False
    paused: bool = False
    stop_sending: bool = False
    intiface_url: str = "ws://127.0.0.1:12345"
    max_intensity: float = 1.0
    timeout: float = 3.0
    setup_done: bool = False
    haptics: bool = False
    haptics_send: bool = False
    haptics_self: bool = False
    haptics_touch: bool = True
    haptics_motion: bool = False
    haptics_strength: float = 0.5
    haptics_depth: bool = True
    haptics_flip: bool = False
    haptics_off: list = field(default_factory=list)
    check_updates: bool = True


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def writable(path):
    test = os.path.join(path, ".vibevrc-write-test")
    try:
        os.makedirs(path, exist_ok=True)
        with open(test, "w") as f:
            f.write("ok")
        os.remove(test)
        return True
    except OSError:
        return False


def home():
    path = os.environ.get("VIBEVRC_HOME") or os.path.join(app_dir(), "VibeVRC Data")
    if not writable(path):
        path = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "VibeVRC")
    os.makedirs(path, exist_ok=True)
    return path


def settings_path(home_dir):
    return os.path.join(home_dir, "settings.json")


def fits(value, default):
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(value, bool):
        return False
    if isinstance(default, float):
        return isinstance(value, (int, float)) and math.isfinite(value)
    return isinstance(value, type(default))


def good_port(value, low=1):
    return low <= value <= 65535


def good_url(url):
    try:
        u = urlparse(url)
        return u.scheme == "ws" and bool(u.hostname) and u.port != 0
    except ValueError:
        return False


def clean_targets(raw):
    out, seen = [], set()
    for t in raw:
        if not isinstance(t, (list, tuple)) or len(t) != 2 or not all(isinstance(x, str) for x in t):
            continue
        try:
            code = format_code(normalize_code(t[1]))
        except ValueError:
            continue
        if code not in seen and t[0].strip() and len(out) < 8:
            seen.add(code)
            out.append((t[0].strip()[:32], code))
    return out


def read(path):
    for i in range(5):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except PermissionError:
            if i == 4:
                raise
            time.sleep(0.2)


def load(home_dir):
    path = settings_path(home_dir)
    cfg = Config()
    try:
        data = read(path)
    except FileNotFoundError:
        data = None
    except ValueError:
        data = None
        try:
            os.replace(path, path + ".broken")
        except OSError:
            pass
    except OSError:
        return cfg
    if not isinstance(data, dict):
        try:
            save(cfg, home_dir)
        except OSError:
            pass
        return cfg
    defaults = dataclasses.asdict(cfg)
    for k, v in data.items():
        if k in defaults and fits(v, defaults[k]):
            setattr(cfg, k, float(v) if isinstance(defaults[k], float) else v)
    checks = {
        "osc_port": good_port(cfg.osc_port, 0),
        "send_port": good_port(cfg.send_port),
        "relay_port": good_port(cfg.relay_port),
        "relay_host": bool(cfg.relay_host.strip()),
        "send_ip": bool(cfg.send_ip.strip()),
        "intiface_url": good_url(cfg.intiface_url),
        "timeout": 1 <= cfg.timeout <= 30,
    }
    for k, ok in checks.items():
        if not ok:
            setattr(cfg, k, defaults[k])
    cfg.max_intensity = max(0.0, min(1.0, cfg.max_intensity))
    cfg.haptics_strength = max(0.0, min(1.0, cfg.haptics_strength))
    cfg.name = cfg.name.strip()[:32]
    cfg.targets = clean_targets(cfg.targets)
    cfg.haptics_off = sorted({x for x in cfg.haptics_off if isinstance(x, str)})
    return cfg


def save(cfg, home_dir):
    tmp = settings_path(home_dir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dataclasses.asdict(cfg), f, indent=2)
    os.replace(tmp, settings_path(home_dir))

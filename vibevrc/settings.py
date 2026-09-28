import dataclasses
import json
import os
import sys
from dataclasses import dataclass, field


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
    haptics_off: list = field(default_factory=list)


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


def load(home_dir):
    try:
        with open(settings_path(home_dir), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        cfg = Config()
        save(cfg, home_dir)
        return cfg
    cfg = Config()
    for k, v in data.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    cfg.targets = [tuple(t) for t in cfg.targets]
    return cfg


def save(cfg, home_dir):
    tmp = settings_path(home_dir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dataclasses.asdict(cfg), f, indent=2)
    os.replace(tmp, settings_path(home_dir))

import math
import time

PREFIX = "/avatar/parameters/"
PARTIES = ("Others", "Self")
DEAD_ZONE = 0.03
ATTACK = 0.04
RELEASE = 0.25
MOTION_RELEASE = 0.45
MOTION_SMOOTH = 0.12
MOTION_FULL = 1.5
STALE = 30.0
NAMES = {"Orf": "Socket", "Pen": "Plug", "Touch": "Touch"}


def parse(address):
    if not address.startswith(PREFIX):
        return None
    parts = address[len(PREFIX):].split("/")
    if len(parts) == 4 and parts[0] == "OGB" and parts[1] in ("Orf", "Pen"):
        return parts[1], parts[2], parts[3]
    if len(parts) == 5 and parts[:3] == ["VFH", "Zone", "Touch"]:
        return "Touch", parts[3], parts[4]
    return None


def flt(value):
    if isinstance(value, bool):
        return float(value)
    value = float(value)
    return max(0.0, min(1.0, value)) if math.isfinite(value) else 0.0


class Length:
    def __init__(self):
        self.samples = []
        self.fallback = 0.0

    def clear(self):
        self.samples = []
        self.fallback = 0.0

    def update(self, root, tip):
        if root < 0.01 or tip < 0.01:
            self.clear()
        elif root > 0.95 or tip - root < 0.02:
            return
        elif tip > 0.99:
            self.fallback = max(self.fallback, tip - root)
        else:
            self.samples = (self.samples + [tip - root])[-8:]

    def value(self):
        if len(self.samples) < 4:
            return self.fallback or None
        s = sorted(self.samples)
        i = min(range(len(s) - 1), key=lambda k: s[k + 1] - s[k])
        return s[i + 1]


class Zone:
    def __init__(self, kind, zid):
        self.kind = kind
        self.id = zid
        self.v = {}
        self.lengths = {p: Length() for p in PARTIES}
        self.prev = None
        self.speed = 0.0
        self.level = 0.0
        self.fresh = set()

    @property
    def key(self):
        return f"{self.kind}/{self.id}"

    @property
    def label(self):
        return f"{self.id.replace('_', ' / ')} ({NAMES[self.kind]})"

    def set(self, key, value):
        self.v[key] = flt(value)
        if key.endswith(("NewRoot", "NewTip")):
            self.fresh.add(key)

    def measure(self):
        for p in PARTIES:
            r, t = f"Pen{p}NewRoot", f"Pen{p}NewTip"
            root, tip = self.v.get(r, 0.0), self.v.get(t, 0.0)
            both = r in self.fresh and t in self.fresh
            either = r in self.fresh or t in self.fresh
            if both or either and (tip > 0.99 or min(root, tip) < 0.01 or root > 0.95):
                self.lengths[p].update(root, tip)
                self.fresh.discard(r)
                self.fresh.discard(t)

    def gated(self, key):
        close = self.v.get(key + "Close")
        return self.v.get(key, 0.0) if close is None or close > 0 else 0.0

    def depth(self, party):
        root = self.v.get(f"Pen{party}NewRoot", 0.0)
        tip = self.v.get(f"Pen{party}NewTip", 0.0)
        if root > 0 or tip > 0:
            length = self.lengths[party].value()
            if length and tip > 0.99:
                return max(0.0, min(1.0, 1 - (1 - root) / length))
            return 0.0
        if party == "Others":
            return self.gated("PenOthers")
        return 0.0

    def sources(self, self_touch, touch):
        out = []
        parties = PARTIES if self_touch else PARTIES[:1]
        for p in parties:
            if self.kind == "Orf":
                out.append(self.depth(p))
            elif self.kind == "Pen":
                out.append(self.v.get(f"Pen{p}", 0.0))
            if touch:
                out.append(self.v.get(p, 0.0) if self.kind == "Touch" else self.gated(f"Touch{p}"))
        if self.kind == "Orf":
            out.append(self.v.get("FrotOthers", 0.0))
        elif self.kind == "Pen":
            out.append(self.gated("FrotOthers"))
        return out


class Haptics:
    def __init__(self):
        self.zones = {}
        self.off = set()
        self.self_touch = False
        self.touch = True
        self.motion = False
        self.gain = 1.0
        self.level = 0.0
        self.version = None
        self.heard = 0.0
        self.last_tick = None
        self.mode = None

    def on_osc(self, address, value):
        if address == PREFIX + "VFH/Version/10" or address == PREFIX + "VFH/Version/9":
            self.version = address.rsplit("/", 1)[1]
            return True
        found = parse(address)
        if not found:
            return False
        kind, zid, key = found
        zone = self.zones.get(f"{kind}/{zid}")
        if zone is None:
            zone = self.zones[f"{kind}/{zid}"] = Zone(kind, zid)
        try:
            zone.set(key, value)
        except (TypeError, ValueError):
            pass
        return True

    def clear(self):
        self.zones = {}
        self.version = None
        self.level = 0.0

    def reset_motion(self):
        for z in self.zones.values():
            z.prev = None
            z.speed = 0.0

    def tick(self, now=None):
        now = time.monotonic() if now is None else now
        dt = 0.05 if self.last_tick is None else max(0.001, min(0.25, now - self.last_tick))
        self.last_tick = now
        stale = self.heard and now - self.heard > STALE
        mode = (self.self_touch, self.touch, self.motion)
        if stale or mode != self.mode:
            self.mode = mode
            self.reset_motion()
        raw = 0.0
        for z in self.zones.values() if not stale else ():
            z.measure()
            value = max(z.sources(self.self_touch, self.touch), default=0.0)
            if z.prev is not None:
                speed = abs(value - z.prev) / dt
                z.speed += (speed - z.speed) * min(1.0, dt / MOTION_SMOOTH)
            z.prev = value
            z.level = min(1.0, z.speed / MOTION_FULL) if self.motion else value
            if z.key not in self.off:
                raw = max(raw, z.level)
        target = max(0.0, (raw - DEAD_ZONE) / (1 - DEAD_ZONE))
        target = min(1.0, target * self.gain)
        tau = ATTACK if target > self.level else MOTION_RELEASE if self.motion else RELEASE
        self.level += (target - self.level) * min(1.0, dt / tau)
        if self.level < 0.005 and target == 0:
            self.level = 0.0
        return self.level

    def active(self):
        return bool(self.level) or any(any(z.v.values()) for z in self.zones.values())

    def zone_list(self):
        return [{"key": z.key, "name": z.label, "level": round(z.level, 3), "on": z.key not in self.off}
                for z in sorted(self.zones.values(), key=lambda z: (z.kind != "Orf", z.kind != "Pen", z.id.lower()))]

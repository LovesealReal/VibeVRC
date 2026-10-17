import asyncio
import secrets
import time

from . import patterns
from .relay import Channel

PARAM = "/avatar/parameters/VibeVRC/"
AVATAR_CHANGE = "/avatar/change"
OFF = (False, 0.0, 0)
SEND_INTERVAL = 0.1
HEARTBEAT = 1.0
HAPTIC_HOLD = 3.0
NOBODY = 255


def clean(text, size=32):
    return "".join(c for c in str(text) if c.isprintable())[:size]


class Target:
    def __init__(self, nick, code):
        self.nick = nick
        self.code = code
        self.ch = Channel(code)
        self.online = None
        self.toys = []
        self.paused = False
        self.sent = OFF
        self.sent_at = 0.0
        self.test_until = 0.0


class Controller:
    def __init__(self, name, relay, osc, log):
        self.name = name
        self.relay = relay
        self.osc = osc
        self.log = log
        self.targets = []
        self.sid = secrets.token_hex(8)
        self.seq = 0
        self.active = False
        self.intensity = 0.5
        self.pattern = 0
        self.target = 0
        self.all = False
        self.panic = False
        self.haptic = lambda: 0.0
        self.held = {}
        self.touching = set()
        self.local = lambda t: None

    def osc_param(self, name, value):
        if name == "Active":
            self.active = bool(value)
        elif name == "Intensity":
            self.intensity = max(0.0, min(1.0, float(value)))
        elif name == "Pattern":
            self.pattern = int(value)
        elif name == "Target":
            self.target = int(value)
            self.report_selected()
        elif name == "All":
            self.all = bool(value)

    def avatar_changed(self):
        self.active = False
        self.all = False

    def set_panic(self, panic):
        if panic == self.panic:
            return
        self.panic = panic
        self.log("Stopped sending." if panic else "Sending again.")

    def removed(self, idx):
        self.held = {i - (i > idx): v for i, v in self.held.items() if i != idx}
        self.touching = {i - (i > idx) for i in self.touching if i != idx}
        if self.target == idx:
            self.target = NOBODY
        elif idx < self.target <= len(self.targets):
            self.target -= 1
        else:
            return
        self.tell("Target", self.target)
        self.report_selected()

    def tell(self, name, value):
        try:
            self.osc.send_message(PARAM + name, value)
        except OSError:
            pass

    def on_status(self, target, payload):
        if self.local(target):
            return
        st = target.ch.open(payload)
        if st is None or "online" not in st:
            return
        self.apply_status(target, st)

    def apply_status(self, target, st):
        online = bool(st.get("online"))
        toys = st.get("toys")
        toys = [clean(t, 64) for t in toys[:16]] if isinstance(toys, list) else []
        paused = bool(st.get("paused"))
        if online != target.online or toys != target.toys or paused != target.paused:
            if not online:
                self.log(f"{target.nick} is offline.")
            else:
                what = ", ".join(toys) if toys else "no toy yet"
                self.log(f"{target.nick} is online ({what})" + (", but paused." if paused else "."))
        target.online = online
        target.toys = toys
        target.paused = paused
        self.report_selected()

    def report_selected(self):
        t = self.targets[self.target] if 0 <= self.target < len(self.targets) else None
        ready = bool(t and t.online and t.toys and not t.paused)
        self.tell("TargetOnline", ready)

    def desired(self, idx, now=None):
        now = time.monotonic() if now is None else now
        self.touching.discard(idx)
        if self.panic:
            return OFF
        if self.targets[idx].test_until > now:
            return (True, 0.5, 0)
        if not (self.all or self.target == idx):
            return OFF
        cmd = OFF
        if self.active:
            pat = self.pattern if self.pattern in range(len(patterns.NAMES)) else 0
            cmd = (True, round(self.intensity, 2), pat)
        level = self.haptic()
        if level > 0.01:
            self.held[idx] = now + HAPTIC_HOLD
        if self.held.get(idx, 0) > now:
            if cmd[0]:
                cmd = (True, round(max(cmd[1], level), 2), cmd[2])
            else:
                cmd = (True, round(level, 2), 0)
                self.touching.add(idx)
        return cmd

    def send(self, t, cmd, touch=False):
        self.seq += 1
        msg = {"sid": self.sid, "seq": self.seq, "ts": time.time(), "name": self.name or "Someone",
               "on": cmd[0], "i": cmd[1], "p": cmd[2]}
        rx = self.local(t)
        if rx:
            rx.take(msg)
        else:
            self.relay.publish(t.ch.cmd_topic, t.ch.seal(msg), qos=0)
        if cmd[0] != t.sent[0] or (cmd[0] and cmd[2] != t.sent[2]):
            if cmd[0] and touch:
                self.log(f"{t.nick}: sending your avatar's touches")
            elif cmd[0]:
                self.log(f"{t.nick}: {patterns.NAMES[cmd[2]]} {cmd[1]:.0%}")
            else:
                self.log(f"{t.nick}: stopped")
        t.sent = cmd
        t.sent_at = time.monotonic()

    def stop(self, t):
        if t.sent[0]:
            self.send(t, OFF)

    async def run(self):
        while True:
            now = time.monotonic()
            for idx, t in enumerate(self.targets):
                rx = self.local(t)
                if rx:
                    st = rx.status()
                    if (st["online"], st["toys"], st["paused"]) != (t.online, t.toys, t.paused):
                        self.apply_status(t, st)
                cmd = self.desired(idx, now)
                since = now - t.sent_at
                if (cmd != t.sent and since >= SEND_INTERVAL) or (cmd[0] and since >= HEARTBEAT):
                    self.send(t, cmd, idx in self.touching)
            await asyncio.sleep(0.05 if self.targets else 0.5)

    def stop_all(self):
        for t in self.targets:
            self.stop(t)

    async def test(self, idx, seconds=3.0):
        if not 0 <= idx < len(self.targets):
            return
        t = self.targets[idx]
        if self.panic:
            self.log("Sending is stopped. Turn it back on to test.")
            return
        self.log(f"Testing {t.nick} at 50% for {seconds:.0f}s.")
        t.test_until = time.monotonic() + seconds
        await asyncio.sleep(seconds + 0.3)

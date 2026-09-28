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
        self.panic = panic
        self.log("Stopped sending." if panic else "Sending again.")

    def on_status(self, target, payload):
        st = target.ch.open(payload)
        if st is None:
            return
        online = bool(st.get("online"))
        toys = list(st.get("toys", []))
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
        self.osc.send_message(PARAM + "TargetOnline", ready)

    def desired(self, idx):
        if self.targets[idx].test_until > time.monotonic():
            return (True, 0.5, 0)
        if self.panic:
            return OFF
        if not self.active or not (self.all or self.target == idx):
            return OFF
        pat = self.pattern if self.pattern in range(len(patterns.NAMES)) else 0
        return (True, round(self.intensity, 2), pat)

    def send(self, t, cmd):
        self.seq += 1
        msg = {"sid": self.sid, "seq": self.seq, "ts": time.time(), "name": self.name or "Someone",
               "on": cmd[0], "i": cmd[1], "p": cmd[2]}
        self.relay.publish(t.ch.cmd_topic, t.ch.seal(msg))
        if cmd[0] != t.sent[0] or (cmd[0] and cmd[2] != t.sent[2]):
            if cmd[0]:
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
                cmd = self.desired(idx)
                since = now - t.sent_at
                if cmd != t.sent and since >= SEND_INTERVAL:
                    self.send(t, cmd)
                elif cmd[0] and since >= HEARTBEAT:
                    self.send(t, cmd)
            await asyncio.sleep(0.05 if self.targets else 0.5)

    def stop_all(self):
        for t in self.targets:
            self.stop(t)

    async def test(self, idx, seconds=3.0):
        t = self.targets[idx]
        self.log(f"Testing {t.nick} at 50% for {seconds:.0f}s.")
        t.test_until = time.monotonic() + seconds
        await asyncio.sleep(seconds + 0.3)

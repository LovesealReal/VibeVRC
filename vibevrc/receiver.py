import asyncio
import math
import time

from . import patterns
from .controller import PARAM, clean

MAX_CLOCK_SKEW = 60
SKEW_GRACE = 3


class Receiver:
    def __init__(self, channel, toys, relay, osc, max_intensity, timeout, log, verbose=False, seqs=None):
        self.ch = channel
        self.toys = toys
        self.relay = relay
        self.osc = osc
        self.max_intensity = max(0.0, min(1.0, max_intensity))
        self.timeout = timeout
        self.log = log
        self.verbose = verbose
        self.paused = False
        self.controllers = {}
        self.seqs = {} if seqs is None else seqs
        self.skew_warned = set()
        self.skewed = {}
        self.level = 0.0
        self.sent_level = None
        self.last_status = 0.0
        self.remote = True
        self.haptic = lambda: 0.0
        self.depth = lambda: None
        self.fast = lambda: False
        self.local = 0.0

    def on_cmd(self, payload):
        if not self.remote:
            return
        msg = self.ch.open(payload)
        if msg is None:
            if self.verbose:
                self.log("Ignored a message with the wrong code.")
            return
        self.take(msg)

    def take(self, msg):
        try:
            sid = str(msg["sid"])[:64]
            seq = int(msg["seq"])
            ts = float(msg["ts"])
            name = clean(msg.get("name") or "Someone") or "Someone"
            on = bool(msg["on"])
            inten = max(0.0, min(1.0, float(msg.get("i", 0))))
            pat = int(msg.get("p", 0))
        except (KeyError, TypeError, ValueError, OverflowError):
            return
        now = time.monotonic()
        if not math.isfinite(ts) or abs(time.time() - ts) > MAX_CLOCK_SKEW:
            first = self.skewed.setdefault(sid, now)
            if now - first > SKEW_GRACE and sid not in self.skew_warned and math.isfinite(ts):
                self.skew_warned.add(sid)
                self.log(f"Blocked {name}: your clocks are {abs(time.time() - ts):.0f}s apart. "
                         "Both of you turn on Set time automatically in Windows.")
            return
        self.skewed.pop(sid, None)
        self.skew_warned.discard(sid)
        last = self.seqs.get(sid)
        if last and seq <= last[0]:
            return
        self.seqs[sid] = (seq, now)

        ctl = self.controllers.get(sid)
        if not on:
            if ctl:
                del self.controllers[sid]
                self.log(f"{ctl['name']} stopped.")
            return
        if pat not in range(len(patterns.NAMES)):
            pat = 0
        if ctl is None:
            self.controllers[sid] = {"name": name, "start": now, "p": pat, "i": inten, "seen": now}
            self.log(f"{name} is controlling your toy ({patterns.NAMES[pat]} {inten:.0%})"
                     + (", but you're paused." if self.paused else "."))
            return
        if ctl["p"] != pat:
            ctl["start"] = now
            ctl["p"] = pat
            self.log(f"{name} switched to {patterns.NAMES[pat]}.")
        ctl.update(i=inten, seen=now)

    def publish_status(self):
        if not self.remote:
            return
        self.relay.publish(self.ch.status_topic, self.ch.seal(self.status()), retain=True)

    def status(self):
        return {"online": True, "toys": self.toys.toy_names, "paused": self.paused}

    def offline_payload(self):
        return self.ch.seal({"online": False})

    def set_paused(self, paused):
        if paused == self.paused:
            return
        self.paused = paused
        self.publish_status()
        self.log("Paused. Nobody can control your toy." if paused else "Resumed.")

    async def run(self):
        failed = False
        while True:
            try:
                await self.step()
                failed = False
            except Exception as e:
                self.controllers.clear()
                self.level = 0.0
                if not failed:
                    self.log(f"Toy control had a problem, so your toy stopped: {e!r}")
                failed = True
                try:
                    await self.toys.set_level(0.0)
                except Exception:
                    pass
            await asyncio.sleep(0.05 if self.controllers or self.level or self.fast() else 0.25)

    async def step(self):
        now = time.monotonic()
        for sid, c in list(self.controllers.items()):
            if now - c["seen"] > self.timeout:
                del self.controllers[sid]
                self.log(f"Lost {c['name']}. Stopped their input.")
        for sid, (_, t) in list(self.seqs.items()):
            if now - t > MAX_CLOCK_SKEW * 2:
                del self.seqs[sid]

        level = 0.0
        self.local = 0.0 if self.paused else self.haptic()
        if not self.paused:
            for c in self.controllers.values():
                level = max(level, patterns.level(c["p"], c["i"], now - c["start"]))
            level = max(level, self.local)
        level *= self.max_intensity
        self.level = level
        depth = None if self.paused else self.depth()
        await self.toys.set_depth(None if depth is None else depth * self.max_intensity)
        await self.toys.set_level(level)

        last = self.sent_level
        if last is None or abs(level - last) > 0.02 or (level == 0) != (last == 0):
            self.sent_level = level
            try:
                self.osc.send_message(PARAM + "Receiving", float(level))
            except OSError:
                pass
        if self.remote and now - self.last_status > 60 and self.relay.connected:
            self.publish_status()
            self.last_status = now

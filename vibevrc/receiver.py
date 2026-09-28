import asyncio
import time

from . import patterns
from .controller import PARAM

MAX_CLOCK_SKEW = 60


class Receiver:
    def __init__(self, channel, toys, relay, osc, max_intensity, timeout, log, verbose=False):
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
        self.seqs = {}
        self.skew_warned = set()
        self.level = 0.0
        self.sent_level = None

    def on_cmd(self, payload):
        msg = self.ch.open(payload)
        if msg is None:
            if self.verbose:
                self.log("Ignored a message with the wrong code.")
            return
        try:
            sid = str(msg["sid"])
            seq = int(msg["seq"])
            ts = float(msg["ts"])
            name = str(msg.get("name") or "Someone")[:32]
            on = bool(msg["on"])
            inten = max(0.0, min(1.0, float(msg.get("i", 0))))
            pat = int(msg.get("p", 0))
        except (KeyError, TypeError, ValueError):
            return
        if abs(time.time() - ts) > MAX_CLOCK_SKEW:
            if sid not in self.skew_warned:
                self.skew_warned.add(sid)
                self.log(f"Blocked {name}: your clocks are {abs(time.time() - ts):.0f}s apart. "
                         "Both of you turn on Set time automatically in Windows.")
            return
        now = time.monotonic()
        last = self.seqs.get(sid)
        if last and seq <= last[0]:
            return
        self.seqs[sid] = (seq, now)

        ctl = self.controllers.get(sid)
        if not on:
            if ctl:
                self.log(f"{ctl['name']} stopped.")
                del self.controllers[sid]
            return
        if pat not in range(len(patterns.NAMES)):
            pat = 0
        if ctl is None:
            ctl = self.controllers[sid] = {"name": name, "start": now, "p": pat}
            self.log(f"{name} is controlling your toy ({patterns.NAMES[pat]} {inten:.0%})"
                     + (", but you're paused." if self.paused else "."))
        elif ctl["p"] != pat:
            ctl["start"] = now
            self.log(f"{name} switched to {patterns.NAMES[pat]}.")
        ctl.update(i=inten, p=pat, seen=now)

    def publish_status(self):
        status = {"online": True, "toys": self.toys.toy_names, "paused": self.paused}
        self.relay.publish(self.ch.status_topic, self.ch.seal(status), retain=True)

    def offline_payload(self):
        return self.ch.seal({"online": False})

    def set_paused(self, paused):
        if paused == self.paused:
            return
        self.paused = paused
        self.log("Paused. Nobody can control your toy." if paused else "Unpaused.")
        self.publish_status()

    def osc_param(self, name, value):
        if name == "Block":
            self.set_paused(bool(value))

    async def run(self):
        last_status = 0.0
        while True:
            now = time.monotonic()
            for sid, c in list(self.controllers.items()):
                if now - c["seen"] > self.timeout:
                    self.log(f"Lost {c['name']}. Stopped their input.")
                    del self.controllers[sid]
            for sid, (_, t) in list(self.seqs.items()):
                if now - t > MAX_CLOCK_SKEW * 2:
                    del self.seqs[sid]

            level = 0.0
            if not self.paused:
                for c in self.controllers.values():
                    level = max(level, patterns.level(c["p"], c["i"], now - c["start"]))
            level *= self.max_intensity
            self.level = level
            await self.toys.set_level(level)

            last = self.sent_level
            if last is None or abs(level - last) > 0.02 or (level == 0) != (last == 0):
                self.osc.send_message(PARAM + "Receiving", float(level))
                self.sent_level = level
            if now - last_status > 60 and self.relay.connected:
                self.publish_status()
                last_status = now
            await asyncio.sleep(0.05 if self.controllers or level else 0.25)

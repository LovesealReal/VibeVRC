import asyncio
import collections
import math
import os
import time

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import AsyncIOOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient

from . import patterns, settings
from .controller import AVATAR_CHANGE, PARAM, Controller, Target
from .engine import EngineSupervisor, find_engine, is_remote
from .haptics import Haptics
from .intiface import Intiface
from .oscquery import OscQuery
from .receiver import Receiver
from .relay import Channel, Relay, format_code, new_share_code, normalize_code

ENABLE = "/avatar/parameters/OGB_ENABLED"
HAPTIC_MENU = {"Haptics": "haptics", "HapticsSend": "haptics_send", "HapticsSelf": "haptics_self",
               "HapticsTouch": "haptics_touch", "HapticsMotion": "haptics_motion", "HapticsStrength": "haptics_strength"}


def open_log(home):
    path = os.path.join(home, "vibevrc.log")
    try:
        if os.path.exists(path):
            os.replace(path, os.path.join(home, "vibevrc-previous.log"))
        return open(path, "w", encoding="utf-8", buffering=1)
    except OSError:
        return None


class Core:
    def __init__(self, cfg, home, echo=None, verbose=False):
        self.cfg = cfg
        self.home = home
        self.echo = echo
        self.verbose = verbose
        self.code_path = os.path.join(home, "share_code.txt")
        self.engine_path = find_engine()
        self.engine_log = os.path.join(home, "engine.log")
        self.lines = collections.deque(maxlen=300)
        self.logf = open_log(home)
        self.osc_seen = 0.0
        self.osc_error = None
        self.osc_out = SimpleUDPClient(cfg.send_ip, cfg.send_port)
        self.transport = None
        self.oscquery = None
        self.relay = None
        self.controller = None
        self.receiver = None
        self.intiface = None
        self.supervisor = None
        self.code = None
        self.tasks = []
        self.share_tasks = []
        self.haptics = Haptics()
        self.enabled = False
        self.enable_sent = 0.0
        self.menu_quiet = 0.0
        self.lock = asyncio.Lock()
        self.save_timer = None
        self.apply_haptics()

    def log(self, msg):
        line = time.strftime("[%H:%M:%S] ") + msg
        self.lines.append(line)
        if self.logf:
            self.logf.write(line + "\n")
        if self.echo:
            self.echo(line)

    def spawn(self, coro):
        task = asyncio.ensure_future(coro)
        name = getattr(coro, "__qualname__", "task")

        def report(t):
            if not t.cancelled() and t.exception():
                self.log(f"Error in {name}: {t.exception()}")

        task.add_done_callback(report)
        return task

    def load_code(self, regenerate=False):
        if not regenerate:
            code = self.peek_code()
            if code:
                return code
            if os.path.exists(self.code_path):
                self.log("share_code.txt was broken, so there's a new code. Send it to your partner again.")
        code = new_share_code()
        with open(self.code_path, "w", encoding="utf-8") as f:
            f.write(code + "\n")
        return code

    def peek_code(self):
        try:
            with open(self.code_path, encoding="utf-8") as f:
                return format_code(normalize_code(f.read()))
        except (OSError, ValueError):
            return ""

    async def start(self, listen=True, new_code=False, discover=True):
        cfg = self.cfg
        self.relay = Relay(cfg.relay_host, cfg.relay_port, cfg.relay_tls, self.log)
        self.controller = Controller(cfg.name, self.relay, self.osc_out, self.log)
        self.controller.haptic = lambda: self.haptics.level if self.cfg.haptics_send else 0.0
        for nick, code in cfg.targets:
            self.attach_target(nick, code)
        self.relay.on_connect.append(self.on_relay_connect)
        if self.toy_wanted():
            self.prepare_share(new_code)
        self.relay.start()
        if listen:
            await self.listen(discover)
        self.tasks.append(self.spawn(self.controller.run()))
        self.tasks.append(self.spawn(self.haptics_loop()))
        if self.receiver:
            self.run_share()

    async def listen(self, discover=True):
        disp = Dispatcher()
        disp.map(PARAM + "*", self.on_param)
        disp.map(AVATAR_CHANGE, self.on_avatar_change)
        disp.set_default_handler(self.on_any_osc)
        fixed = self.cfg.osc_port
        try:
            server = AsyncIOOSCUDPServer(("127.0.0.1", fixed), disp, asyncio.get_running_loop())
            self.transport, _ = await server.create_serve_endpoint()
        except OSError:
            self.osc_error = f"Port {fixed} is in use by another app."
            self.log(self.osc_error)
            return
        port = self.transport.get_extra_info("sockname")[1]
        if fixed:
            self.log(f"Listening for VRChat on port {port}.")
            return
        if discover:
            await self.start_discovery()

    async def start_discovery(self):
        if self.oscquery or not self.transport or self.cfg.osc_port:
            return
        self.oscquery = OscQuery(self.transport.get_extra_info("sockname")[1])
        try:
            await asyncio.to_thread(self.oscquery.start)
        except OSError as e:
            self.oscquery = None
            self.osc_error = "Couldn't let VRChat know VibeVRC is running."
            self.log(f"{self.osc_error} ({e})")
            return
        self.log("Waiting for VRChat. Make sure OSC is on in VRChat.")

    def heard(self):
        if not self.osc_seen:
            self.spawn(self.push_menu(0))
        self.osc_seen = time.time()
        self.haptics.heard = time.monotonic()

    def on_any_osc(self, address, *vals):
        self.heard()
        if vals:
            self.haptics.on_osc(address, vals[0])

    def on_avatar_change(self, *_):
        self.controller.avatar_changed()
        self.haptics.clear()
        self.menu_quiet = time.monotonic() + 2
        self.spawn(self.push_menu(2))
        if self.haptics_wanted():
            self.send_enable(True)

    async def push_menu(self, delay):
        await asyncio.sleep(delay)
        for name, attr in HAPTIC_MENU.items():
            self.osc_out.send_message(PARAM + name, getattr(self.cfg, attr))

    def on_param(self, address, *vals):
        if not vals:
            return
        self.heard()
        name = address[len(PARAM):]
        attr = HAPTIC_MENU.get(name)
        if attr:
            raw = vals[0]
            if attr == "haptics_strength":
                if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
                    return
                value = max(0.0, min(1.0, float(raw)))
            elif isinstance(raw, (bool, int)):
                value = bool(raw)
            else:
                return
            if getattr(self.cfg, attr) != value and time.monotonic() > self.menu_quiet:
                self.spawn(self.set_haptics(False, **{attr: value}))
            return
        try:
            self.controller.osc_param(name, vals[0])
            if self.receiver:
                self.receiver.osc_param(name, vals[0])
        except (TypeError, ValueError):
            pass

    def on_relay_connect(self):
        if self.receiver:
            self.receiver.publish_status()

    def attach_target(self, nick, code):
        t = Target(nick, code)
        self.controller.targets.append(t)
        self.relay.subscribe(t.ch.status_topic, lambda p: self.controller.on_status(t, p))

    def add_target(self, nick, code):
        code = format_code(normalize_code(code))
        if any(t.code == code for t in self.controller.targets):
            return False
        self.attach_target(nick, code)
        self.cfg.targets.append((nick, code))
        self.log(f"Added {nick} as Target {len(self.controller.targets)}.")
        return True

    def remove_target(self, idx):
        t = self.controller.targets[idx]
        self.controller.stop(t)
        self.relay.unsubscribe(t.ch.status_topic)
        del self.controller.targets[idx]
        del self.cfg.targets[idx]
        self.log(f"Removed {t.nick}.")

    def set_name(self, name):
        self.cfg.name = name
        self.controller.name = name

    def prepare_share(self, new_code=False):
        cfg = self.cfg
        self.code = self.load_code(new_code)
        ch = Channel(self.code)
        self.supervisor = EngineSupervisor(cfg.intiface_url, self.engine_path, self.engine_log, self.log,
                                           lambda: self.intiface is not None and self.intiface.ws is not None)
        self.intiface = Intiface(cfg.intiface_url, self.log, remote=self.supervisor.remote)
        self.receiver = Receiver(ch, self.intiface, self.relay, self.osc_out, cfg.max_intensity, cfg.timeout,
                                 self.log, self.verbose)
        self.receiver.haptic = lambda: self.haptics.level if self.cfg.haptics else 0.0
        self.receiver.fast = lambda: self.cfg.haptics
        self.receiver.remote = cfg.share
        if cfg.share:
            self.relay.set_will(ch.status_topic, self.receiver.offline_payload())
            self.relay.subscribe(ch.cmd_topic, self.receiver.on_cmd)
        self.intiface.on_change = self.toys_changed
        if self.supervisor.remote:
            self.log(f"Using Intiface on the phone at {self.supervisor.host}:{self.supervisor.port}.")

    def toys_changed(self):
        if self.relay.connected and self.receiver:
            self.receiver.publish_status()

    def run_share(self):
        self.share_tasks = [self.spawn(self.intiface.run()), self.spawn(self.receiver.run()),
                            self.spawn(self.supervisor.run())]
        if self.relay.connected:
            self.receiver.publish_status()

    async def stop_share(self, wait=False):
        rx = self.receiver
        if not rx:
            return
        for t in self.share_tasks[1:]:
            t.cancel()
        await self.intiface.stop_all()
        self.share_tasks[0].cancel()
        self.supervisor.stop()
        if rx.remote:
            self.relay.unsubscribe(rx.ch.cmd_topic)
            info = self.relay.publish(rx.ch.status_topic, rx.offline_payload(), retain=True)
            if wait:
                try:
                    info.wait_for_publish(2)
                except (RuntimeError, ValueError):
                    pass
        self.receiver = None
        self.intiface = None
        self.supervisor = None
        self.share_tasks = []

    def toy_wanted(self):
        return self.cfg.share or self.cfg.haptics

    def go_public(self):
        rx = self.receiver
        rx.ch = Channel(self.code)
        rx.remote = True
        self.relay.set_will(rx.ch.status_topic, rx.offline_payload())
        self.relay.subscribe(rx.ch.cmd_topic, rx.on_cmd)
        self.relay.restart()

    def go_private(self):
        rx = self.receiver
        self.relay.unsubscribe(rx.ch.cmd_topic)
        self.relay.publish(rx.ch.status_topic, rx.offline_payload(), retain=True)
        self.relay.clear_will()
        rx.remote = False
        rx.controllers.clear()

    async def refresh_toy(self):
        rx = self.receiver
        if rx and not self.toy_wanted():
            await self.stop_share()
            self.relay.clear_will()
        elif not rx and self.toy_wanted():
            self.prepare_share()
            if self.cfg.share:
                self.relay.restart()
            self.run_share()
        elif rx and rx.remote != self.cfg.share:
            if self.cfg.share:
                self.go_public()
            else:
                self.go_private()

    async def set_sharing(self, on, new_code=False):
        async with self.lock:
            await self.change_sharing(on, new_code)

    async def change_sharing(self, on, new_code=False):
        if new_code and self.receiver and self.receiver.remote:
            await self.stop_share()
            self.prepare_share(True)
            self.relay.restart()
            self.run_share()
            return
        changed = on != self.cfg.share
        self.cfg.share = on
        await self.refresh_toy()
        if changed:
            self.log("Sharing your toy." if on else "Stopped sharing your toy.")

    async def new_code(self):
        async with self.lock:
            await self.change_code()

    async def change_code(self):
        if self.receiver and self.receiver.remote:
            await self.change_sharing(True, new_code=True)
        else:
            self.code = self.load_code(True)
        self.log("Made a new share code. The old one doesn't work anymore.")

    async def set_toy_source(self, url):
        async with self.lock:
            await self.change_toy_source(url)

    async def change_toy_source(self, url):
        if url == self.cfg.intiface_url:
            return
        self.cfg.intiface_url = url
        if self.receiver:
            await self.stop_share()
            self.prepare_share()
            self.run_share()

    def apply_haptics(self):
        h, c = self.haptics, self.cfg
        h.self_touch = bool(c.haptics_self)
        h.touch = bool(c.haptics_touch)
        h.motion = bool(c.haptics_motion)
        h.gain = max(0.0, min(1.0, float(c.haptics_strength))) * 2
        h.off = set(c.haptics_off)

    def haptics_wanted(self):
        return self.cfg.haptics or self.cfg.haptics_send

    def send_enable(self, on):
        self.osc_out.send_message(ENABLE, bool(on))
        self.enabled = bool(on)
        self.enable_sent = time.monotonic()

    def save(self):
        if self.save_timer:
            self.save_timer.cancel()
            self.save_timer = None
        try:
            settings.save(self.cfg, self.home)
        except OSError:
            pass

    def save_soon(self):
        if self.save_timer:
            self.save_timer.cancel()
        self.save_timer = asyncio.get_running_loop().call_later(1.0, self.save)

    async def set_haptics(self, push=True, **changes):
        async with self.lock:
            await self.change_haptics(push, **changes)

    async def change_haptics(self, push=True, **changes):
        was = self.haptics_wanted()
        for k, v in changes.items():
            setattr(self.cfg, k, v)
        self.apply_haptics()
        if "haptics_send" in changes and not self.cfg.haptics_send:
            self.controller.held.clear()
        if push:
            for name, attr in HAPTIC_MENU.items():
                if attr in changes:
                    self.osc_out.send_message(PARAM + name, changes[attr])
        if "haptics" in changes:
            self.log("Your avatar controls your toy now." if self.cfg.haptics
                     else "Your avatar stopped controlling your toy.")
        if "haptics_send" in changes:
            self.log("Sending your avatar's touches to who you control." if self.cfg.haptics_send
                     else "Stopped sending your avatar's touches.")
        if self.haptics_wanted() != was:
            self.send_enable(self.haptics_wanted())
        if push:
            self.save()
        else:
            self.save_soon()
        await self.refresh_toy()

    async def haptics_loop(self):
        failed = False
        while True:
            try:
                now = time.monotonic()
                if self.haptics_wanted() and now - self.enable_sent > 5:
                    self.send_enable(True)
                self.haptics.tick(now)
                failed = False
            except (OSError, ValueError) as e:
                self.haptics.level = 0.0
                if not failed:
                    self.log(f"Haptics had a problem: {e}")
                failed = True
            await asyncio.sleep(0.05 if self.haptics_wanted() or self.haptics.active() else 0.25)

    def set_max(self, v):
        self.cfg.max_intensity = max(0.0, min(1.0, v))
        if self.receiver:
            self.receiver.max_intensity = self.cfg.max_intensity

    def set_paused(self, paused):
        if self.receiver:
            self.receiver.set_paused(paused)

    def state(self, with_log=True):
        rx = self.receiver
        share = {
            "on": self.cfg.share,
            "toy": bool(rx),
            "local": rx.local if rx else 0.0,
            "code": self.code or self.peek_code(),
            "max": self.cfg.max_intensity,
            "phone": is_remote(self.cfg.intiface_url),
            "address": self.cfg.intiface_url.split("://")[-1],
            "engine": None,
            "intiface": False,
            "toys": [],
            "paused": False,
            "people": [],
            "level": 0.0,
        }
        if rx:
            share["engine"] = self.supervisor.mode
            share["intiface"] = self.intiface.ws is not None
            share["toys"] = self.intiface.toy_names
            share["paused"] = rx.paused
            share["level"] = rx.level
            for c in rx.controllers.values():
                share["people"].append({"name": c["name"], "pattern": patterns.NAMES[c["p"]], "intensity": c["i"]})

        targets = []
        for t in self.controller.targets:
            targets.append({"name": t.nick, "online": bool(t.online), "toys": t.toys, "paused": t.paused,
                            "sending": t.sent[0]})
        control = {"name": self.cfg.name, "paused": self.controller.panic, "targets": targets}
        c = self.cfg
        haptics = {"on": c.haptics, "send": c.haptics_send, "self": c.haptics_self, "touch": c.haptics_touch,
                   "motion": c.haptics_motion, "strength": c.haptics_strength, "level": round(self.haptics.level, 3),
                   "zones": self.haptics.zone_list(), "sps": self.haptics.version is not None}

        seen = time.time() - self.osc_seen if self.osc_seen else None
        return {
            "relay": self.relay.connected,
            "osc": {"error": self.osc_error, "seen": seen},
            "share": share,
            "control": control,
            "haptics": haptics,
            "log": list(self.lines)[-80:] if with_log else None,
        }

    async def stop(self):
        for t in self.tasks:
            t.cancel()
        if self.controller:
            self.controller.stop_all()
        await self.stop_share(wait=True)
        if self.enabled:
            self.send_enable(False)
        await asyncio.sleep(0.3)
        if self.relay:
            self.relay.stop()
        if self.oscquery:
            await asyncio.to_thread(self.oscquery.stop)
        if self.transport:
            self.transport.close()
        self.log("Stopped.")
        if self.logf:
            self.logf.close()
            self.logf = None

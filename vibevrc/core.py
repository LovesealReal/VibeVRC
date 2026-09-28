import asyncio
import collections
import os
import time

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import AsyncIOOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient

from . import patterns
from .controller import AVATAR_CHANGE, PARAM, Controller, Target
from .engine import EngineSupervisor, find_engine, is_remote
from .intiface import Intiface
from .oscquery import OscQuery
from .receiver import Receiver
from .relay import Channel, Relay, format_code, new_share_code, normalize_code


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
        for nick, code in cfg.targets:
            self.attach_target(nick, code)
        self.relay.on_connect.append(self.on_relay_connect)
        if cfg.share:
            self.prepare_share(new_code)
        self.relay.start()
        if listen:
            await self.listen(discover)
        self.tasks.append(self.spawn(self.controller.run()))
        if cfg.share:
            self.run_share()

    async def listen(self, discover=True):
        disp = Dispatcher()
        disp.map(PARAM + "*", self.on_param)
        disp.map(AVATAR_CHANGE, lambda *_: self.controller.avatar_changed())
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

    def on_any_osc(self, *_):
        self.osc_seen = time.time()

    def on_param(self, address, *vals):
        if not vals:
            return
        self.osc_seen = time.time()
        name = address[len(PARAM):]
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

    async def set_sharing(self, on, new_code=False):
        if on and self.receiver and not new_code:
            return
        await self.stop_share()
        self.cfg.share = on
        if on:
            self.prepare_share(new_code)
            self.relay.restart()
            self.run_share()
            self.log("Sharing your toy.")
        else:
            self.relay.clear_will()
            self.log("Stopped sharing your toy.")

    async def new_code(self):
        if self.receiver:
            await self.set_sharing(True, new_code=True)
        else:
            self.code = self.load_code(True)
        self.log("Made a new share code. The old one doesn't work anymore.")

    async def set_toy_source(self, url):
        self.cfg.intiface_url = url
        if self.receiver:
            await self.stop_share()
            self.prepare_share()
            self.run_share()

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
            "on": bool(rx),
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

        seen = time.time() - self.osc_seen if self.osc_seen else None
        return {
            "relay": self.relay.connected,
            "osc": {"error": self.osc_error, "seen": seen},
            "share": share,
            "control": control,
            "log": list(self.lines)[-80:] if with_log else None,
        }

    async def stop(self):
        for t in self.tasks:
            t.cancel()
        if self.controller:
            self.controller.stop_all()
        await self.stop_share(wait=True)
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

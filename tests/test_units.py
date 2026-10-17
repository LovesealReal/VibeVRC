import asyncio
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vibevrc import finder, haptics, oscquery, patterns, updater
from vibevrc.intiface import Intiface
from vibevrc.receiver import Receiver
from vibevrc.relay import Channel, new_share_code, normalize_code


class Stub:
    toy_names = []
    connected = False

    def send_message(self, *a):
        pass

    def publish(self, *a, **k):
        pass


def test_codes():
    codes = {new_share_code() for _ in range(200)}
    assert len(codes) == 200
    c = next(iter(codes))
    assert len(c) == 3 + 24 + 5
    assert normalize_code(c.lower().replace("-", " ")) == normalize_code(c)
    for bad in ["", "VV-1234", "hello world", c[:-1]]:
        try:
            normalize_code(bad)
            raise AssertionError(bad)
        except ValueError:
            pass


def test_seal():
    a = Channel(new_share_code())
    b = Channel(new_share_code())
    blob = a.seal({"x": 1})
    assert a.open(blob) == {"x": 1}
    assert b.open(blob) is None
    assert a.open(blob[:-1] + bytes([blob[-1] ^ 1])) is None
    assert a.open(b"junk") is None
    assert a.cmd_topic != b.cmd_topic


def test_replay_and_clock():
    ch = Channel(new_share_code())
    logs = []
    r = Receiver(ch, Stub(), Stub(), Stub(), 1.0, 3, logs.append)
    msg = {"sid": "s1", "seq": 5, "ts": time.time(), "name": "A", "on": True, "i": 0.5, "p": 0}
    blob = ch.seal(msg)
    r.on_cmd(blob)
    assert "s1" in r.controllers
    r.on_cmd(ch.seal({**msg, "seq": 6, "on": False}))
    assert "s1" not in r.controllers
    r.on_cmd(blob)
    assert "s1" not in r.controllers
    r.on_cmd(ch.seal({**msg, "sid": "s2", "ts": time.time() - 600}))
    assert "s2" not in r.controllers
    r.on_cmd(ch.seal({**msg, "sid": "s3", "i": 9, "p": 99}))
    assert r.controllers["s3"]["i"] == 1.0 and r.controllers["s3"]["p"] == 0


def test_clock_warning():
    import types
    from vibevrc import receiver
    clock = [100.0]
    real = receiver.time
    receiver.time = types.SimpleNamespace(time=time.time, monotonic=lambda: clock[0])
    try:
        ch = Channel(new_share_code())
        logs = []
        r = Receiver(ch, Stub(), Stub(), Stub(), 1.0, 3, logs.append)
        msg = {"sid": "s1", "seq": 1, "ts": time.time() - 90, "name": "A", "on": True, "i": 0.5, "p": 0}
        for i in range(20):
            clock[0] += 0.01
            r.on_cmd(ch.seal({**msg, "seq": i + 1}))
        r.on_cmd(ch.seal({**msg, "seq": 30, "ts": time.time()}))
        assert "s1" in r.controllers and not any("clock" in l for l in logs), "an old backlog isn't a clock problem"
        for i in range(5):
            clock[0] += 1
            r.on_cmd(ch.seal({**msg, "seq": 40 + i}))
        assert sum("clock" in l for l in logs) == 1, "a steady stream of old messages is"
        r.on_cmd(ch.seal({**msg, "seq": 50, "ts": time.time()}))
        for i in range(5):
            clock[0] += 1
            r.on_cmd(ch.seal({**msg, "seq": 60 + i}))
        assert sum("clock" in l for l in logs) == 2, "warns again after it was fine for a while"
    finally:
        receiver.time = real


def test_phone_url():
    from vibevrc.gui import phone_url
    assert phone_url("192.168.1.23:12345") == "ws://192.168.1.23:12345"
    assert phone_url(" 192.168.1.23 ") == "ws://192.168.1.23:12345"
    assert phone_url("ws://10.0.0.5:12345/") == "ws://10.0.0.5:12345"
    assert phone_url("my-phone.local:9999") == "ws://my-phone.local:9999"
    for bad in ["", "192.168.1.23:abc", "192.168.1.23:99999", "hello world", "a:b:c"]:
        assert phone_url(bad) is None, bad


def test_patterns():
    for p in range(len(patterns.NAMES) + 1):
        for i in range(500):
            v = patterns.level(p, 0.7, i * 0.037)
            assert 0.0 <= v <= 0.7 + 1e-9
    assert patterns.level(0, 0.7, 5) == 0.7
    assert patterns.level(1, 0.7, 0.2) == 0.7 and patterns.level(1, 0.7, 0.7) == 0.0


def adapter(name, desc, kind, addrs, gateway, metric=25):
    return {"name": name, "desc": desc, "type": kind, "up": True, "addrs": addrs, "gateway": gateway, "metric": metric}


def test_finder_networks():
    real = finder.adapters
    try:
        finder.adapters = lambda: [
            adapter("vEthernet (WSL)", "Hyper-V Virtual Ethernet Adapter #3", 6, [("172.20.0.1", 20)], False),
            adapter("Local Area Connection* 10", "Microsoft Wi-Fi Direct Virtual Adapter #2", 71, [("192.168.137.1", 24)], False),
            adapter("OpenVPN", "TAP-Windows Adapter V9", 6, [("10.8.0.6", 24)], True, 5),
            adapter("Wi-Fi", "MediaTek Wi-Fi 6", 71, [("192.168.4.77", 22), ("192.168.4.78", 22)], True, 35),
            adapter("Tailscale", "Tailscale Tunnel", 53, [("100.64.1.2", 32)], False),
        ]
        nets, mine = finder.networks()
        assert [str(n) for _, n, _ in nets] == ["192.168.4.0/22", "192.168.137.0/24", "10.8.0.0/24"]
        assert {"192.168.4.77", "192.168.4.78"} <= mine
        finder.adapters = lambda: [adapter("vEthernet (External)", "Hyper-V Virtual Ethernet Adapter", 6, [("192.168.1.5", 24)], True)]
        assert [str(n) for _, n, _ in finder.networks()[0]] == ["192.168.1.0/24"]
    finally:
        finder.adapters = real
    assert str(finder.subnet("10.0.3.7", 16)) == "10.0.3.0/24"


def test_finder_search():
    real = finder.probe

    async def probe(ip, port, sem):
        await asyncio.sleep({"a": 0.02, "b": 0.1}[ip])
        return (ip, "Intiface") if ip == "b" else None

    try:
        finder.probe = probe
        assert asyncio.run(finder.search(["a", "b"], 12345, asyncio.Semaphore(4))) == [("b", "Intiface")]
    finally:
        finder.probe = real


def test_haptics_depth():
    h = haptics.Haptics()
    socket = haptics.PREFIX + "OGB/Orf/Pussy/"
    t = 0.0
    for d in (0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.05):
        h.on_osc(socket + "PenOthersNewRoot", 1 - d)
        h.on_osc(socket + "PenOthersNewTip", min(1.0, 1 - max(0.0, d - 0.15)))
        t += 0.05
        h.tick(t)
    zone = h.zones["Orf/Pussy"]
    assert abs(zone.depth("Others") - (1 - 0.05 / 0.15)) < 0.01
    assert zone.depth("Self") == 0.0
    for _ in range(40):
        t += 0.05
        h.tick(t)
    assert abs(h.level - (zone.depth("Others") - 0.03) / 0.97) < 0.01
    h.off.add("Orf/Pussy")
    for _ in range(40):
        t += 0.05
        h.tick(t)
    assert h.level == 0.0
    assert not h.on_osc(haptics.PREFIX + "VibeVRC/Active", True)
    assert haptics.parse(haptics.PREFIX + "OGB/Pen/Canine/PenOthers") == ("Pen", "Canine", "PenOthers")


def test_haptics_motion():
    h = haptics.Haptics()
    h.motion = True
    plug = haptics.PREFIX + "OGB/Pen/Canine/PenOthers"
    t = 0.0
    for i in range(60):
        t += 0.05
        h.on_osc(plug, 0.5 + 0.4 * (1 if (i // 5) % 2 else -1) * ((i % 5) / 5))
        h.tick(t)
    assert h.level > 0.3, h.level
    for _ in range(60):
        t += 0.05
        h.tick(t)
    assert h.level == 0.0


def test_haptics_strength():
    plug = haptics.PREFIX + "OGB/Pen/Canine/PenOthers"

    def run(strength, motion, wave):
        h = haptics.Haptics()
        h.motion = motion
        h.gain = strength * 2
        t, top = 0.0, 0.0
        for i in range(120):
            t += 0.05
            h.on_osc(plug, wave(t))
            top = max(top, h.tick(t))
        return top

    jitter = lambda t: 0.5 + 0.01 * math.sin(t * 60)
    gentle = lambda t: 0.5 + 0.1 * math.sin(t * math.pi)
    hard = lambda t: 0.5 + 0.45 * math.sin(t * 4 * math.pi)
    assert run(0.5, True, jitter) < 0.1
    assert run(0.5, True, gentle) < 0.1
    assert run(0.25, True, gentle) < 0.01
    assert run(0.1, True, hard) > 0.95
    assert run(1.0, True, gentle) > run(0.5, True, gentle)
    assert run(0.25, False, lambda t: 1.0) > 0.99
    assert run(0.25, False, lambda t: 0.5) < 0.3
    assert run(0.0, False, lambda t: 0.8) == 0.0


def test_updater():
    import contextlib
    import hashlib
    import io
    import json
    import tempfile
    assert updater.parse("v1.2") == (1, 2, 0) and updater.parse("1.10.1") > updater.parse("1.9")
    assert updater.parse("beta") is None
    new = b"new exe"
    rel = {"tag_name": "v1.10", "draft": False, "prerelease": False, "assets": [
        {"name": "VibeVRC.exe", "browser_download_url": "https://x/VibeVRC.exe",
         "digest": "sha256:" + hashlib.sha256(new).hexdigest()}]}
    served = {"api": rel, "file": new}
    real_get = updater.get
    updater.get = lambda url, timeout: contextlib.closing(io.BytesIO(
        json.dumps(served["api"]).encode() if url == updater.API else served["file"]))
    try:
        info = updater.check("1.9")
        assert info["version"] == "1.10" and info["url"] == "https://x/VibeVRC.exe"
        assert updater.check("1.10") is None and updater.check("1.11") is None
        served["api"] = dict(rel, prerelease=True)
        assert updater.check("1.0") is None, "pre-releases are for testers"
        served["api"] = dict(rel, assets=[dict(rel["assets"][0], digest=None)])
        assert updater.check("1.0") is None, "no checksum, no update"
        folder = tempfile.mkdtemp(prefix="vibevrc_upd_")
        path = os.path.join(folder, "VibeVRC.exe")
        with open(path, "wb") as f:
            f.write(b"old exe")
        served["file"] = b"tampered"
        try:
            updater.install(info, path)
            raise AssertionError("bad download installed")
        except ValueError:
            pass
        assert open(path, "rb").read() == b"old exe" and os.listdir(folder) == ["VibeVRC.exe"]
        served["file"] = new
        assert updater.install(info, path) == path
        assert open(path, "rb").read() == new and open(path + ".old", "rb").read() == b"old exe"
    finally:
        updater.get = real_get


def test_haptics_seed():
    tree = {"FULL_PATH": "/avatar/parameters", "CONTENTS": {
        "VFH": {"FULL_PATH": "/avatar/parameters/VFH", "CONTENTS": {
            "Version": {"FULL_PATH": "/avatar/parameters/VFH/Version", "CONTENTS": {
                "10": {"FULL_PATH": "/avatar/parameters/VFH/Version/10", "TYPE": "T", "VALUE": [True]}}},
            "Zone": {"FULL_PATH": "/avatar/parameters/VFH/Zone", "CONTENTS": {"Touch": {"CONTENTS": {
                "Head_Pat": {"CONTENTS": {
                    "Others": {"FULL_PATH": "/avatar/parameters/VFH/Zone/Touch/Head_Pat/Others", "VALUE": [0.0]},
                    "Self": {"FULL_PATH": "/avatar/parameters/VFH/Zone/Touch/Head_Pat/Self", "VALUE": [0.0]}}}}}}}}},
        "OGB": {"CONTENTS": {"Orf": {"CONTENTS": {"Pussy": {"CONTENTS": {
            "PenOthers": {"FULL_PATH": "/avatar/parameters/OGB/Orf/Pussy/PenOthers", "VALUE": [0.0]}}}}}}},
        "Junk": {"FULL_PATH": "/avatar/parameters/Junk", "VALUE": []}}}
    values = {}
    oscquery.collect(tree, values)
    assert "/avatar/parameters/Junk" not in values
    h = haptics.Haptics()
    h.on_osc(haptics.PREFIX + "VFH/Zone/Touch/Head_Pat/Others", 0.7)
    h.seed(values)
    assert sorted(h.zones) == ["Orf/Pussy", "Touch/Head_Pat"]
    assert h.zones["Touch/Head_Pat"].v == {"Others": 0.7, "Self": 0.0}
    assert h.version == "10"


def test_haptics_stroke():
    h = haptics.Haptics()
    plug = haptics.PREFIX + "OGB/Pen/Canine/"
    h.on_osc(haptics.PREFIX + "VFH/Zone/Touch/Head/Others", 0.8)
    t = 0.0
    for _ in range(10):
        t += 0.05
        h.tick(t)
    assert h.depth is None and h.level > 0.5, "touch alone doesn't stroke"
    h.on_osc(plug + "PenSelf", 0.7)
    t += 0.05
    h.tick(t)
    assert h.depth is None, "own plug ignored without self"
    h.on_osc(plug + "PenOthers", 0.6)
    for _ in range(10):
        t += 0.05
        h.tick(t)
    assert abs(h.depth - 0.6) < 0.01
    h.on_osc(plug + "PenOthers", 0.0)
    for _ in range(10):
        t += 0.05
        h.tick(t)
    assert h.depth == 0.0, "held at the rest point for a moment"
    for _ in range(30):
        t += 0.05
        h.tick(t)
    assert h.depth is None
    h.off.add("Pen/Canine")
    h.on_osc(plug + "PenOthers", 0.9)
    t += 0.05
    h.tick(t)
    assert h.depth is None, "disabled zones don't stroke"


class FakeWs:
    def __init__(self):
        self.sent = []

    async def send(self, raw):
        import json
        self.sent.append(json.loads(raw)[0])


def test_intiface_depth():
    async def run():
        toy = Intiface("ws://x", lambda m: None)
        toy.ws = FakeWs()
        toy.add({"DeviceIndex": 0, "DeviceName": "Lovense Lush 3",
                 "DeviceMessages": {"ScalarCmd": [{"ActuatorType": "Vibrate"}]}})
        toy.add({"DeviceIndex": 1, "DeviceName": "Lovense Solace Pro", "DeviceMessageTimingGap": 100,
                 "DeviceMessages": {"LinearCmd": [{"ActuatorType": "Position"}],
                                    "ScalarCmd": [{"ActuatorType": "Oscillate"}]}})
        sent = toy.ws.sent
        await toy.set_depth(None)
        await toy.set_level(0.5)
        assert [list(m)[0] for m in sent] == ["ScalarCmd", "ScalarCmd"], "speed mode with no depth"
        sent.clear()
        await toy.set_depth(0.4)
        await toy.set_level(0.5)
        assert sent[0]["ScalarCmd"]["DeviceIndex"] == 1 and sent[0]["ScalarCmd"]["Scalars"][0]["Scalar"] == 0
        assert len(sent) == 1, "lush keeps its level, stroker stops oscillating before it moves"
        sent.clear()
        await toy.set_depth(0.4)
        assert not sent, "waits a gap after stopping"
        toy.devices[1]["sent"] -= 0.2
        await toy.set_depth(0.4)
        await toy.set_level(0.5)
        assert sent == [sent[0]] and sent[0]["LinearCmd"]["Vectors"] == [{"Index": 0, "Duration": 100, "Position": 0.4}]
        sent.clear()
        await toy.set_depth(0.8)
        assert not sent, "rate limited to the device gap"
        toy.devices[1]["sent"] -= 0.2
        await toy.set_depth(0.8)
        assert sent[-1]["LinearCmd"]["Vectors"][0]["Position"] == 0.8
        sent.clear()
        toy.devices[1]["sent"] -= 0.2
        await toy.set_depth(0.805)
        assert not sent, "tiny moves are skipped"
        toy.flip = True
        await toy.set_depth(0.8)
        assert sent[-1]["LinearCmd"]["Vectors"][0]["Position"] == 0.2
        toy.devices[1]["sent"] -= 0.2
        sent.clear()
        await toy.set_depth(None)
        await toy.set_level(0.5)
        assert sent[0]["LinearCmd"]["Vectors"][0] == {"Index": 0, "Duration": 400, "Position": 1.0}, "back to rest"
        assert sent[1]["ScalarCmd"]["Scalars"][0] == {"Index": 0, "Scalar": 0.5, "ActuatorType": "Oscillate"}
        sent.clear()
        await toy.set_depth(None)
        assert not sent
        toy.add({"DeviceIndex": 2, "DeviceName": "Lovense Max 2",
                 "DeviceMessages": {"ScalarCmd": [{"ActuatorType": "Constrict"}]}})
        assert 2 not in toy.devices
    asyncio.run(run())


def test_haptics_close_gate():
    def level(sends):
        h = haptics.Haptics()
        t = 0.0
        for _ in range(20):
            for a, v in sends:
                h.on_osc(haptics.PREFIX + a, v)
            t += 0.05
            h.tick(t)
        return h.level
    assert level([("OGB/Orf/Pussy/TouchOthers", 0.6)]) == 0.0, "a hand near but not in the zone, Close never sent"
    assert level([("OGB/Pen/Canine/TouchOthers", 0.6)]) == 0.0
    assert level([("OGB/Pen/Canine/FrotOthers", 0.6)]) == 0.0
    assert level([("OGB/Orf/Pussy/TouchOthers", 0.6), ("OGB/Orf/Pussy/TouchOthersClose", 1.0)]) > 0.5
    assert level([("OGB/Orf/Pussy/PenOthers", 0.6)]) > 0.5, "old TPS plugs have no Close"
    h = haptics.Haptics()
    h.on_osc(haptics.PREFIX + "OGB/Orf/Pussy/TouchOthers", 0.6)
    h.seed({haptics.PREFIX + "OGB/Orf/Pussy/TouchOthersClose": 0.0, haptics.PREFIX + "OGB/Orf/Pussy/TouchOthers": 0.0})
    assert h.zones["Orf/Pussy"].v == {"TouchOthers": 0.6, "TouchOthersClose": 0.0}, "seed fills missing keys only"


class FakeRelay:
    connected = False

    def __init__(self, *a):
        self.on_connect = []
        self.topics = []

    def publish(self, topic, *a, **k):
        self.topics.append(topic)

        class Info:
            def wait_for_publish(self, t):
                pass
        return Info()

    def start(self):
        pass

    def stop(self):
        pass

    def set_will(self, *a):
        pass

    def clear_will(self):
        pass

    def subscribe(self, *a):
        pass

    def unsubscribe(self, *a):
        pass

    def restart(self):
        pass


def test_pause_survives_rebuild():
    import tempfile
    from vibevrc import core as core_mod, settings

    async def run():
        cfg = settings.Config(share=True, send_port=19099, intiface_url="ws://127.0.0.1:1")
        c = core_mod.Core(cfg, tempfile.mkdtemp(prefix="vibevrc_pause_"))
        c.engine_path = ""
        c.relay = FakeRelay()
        c.prepare_share()
        c.run_share()
        c.set_paused(True)
        await c.change_toy_source("ws://127.0.0.1:2")
        assert c.receiver.paused, "switching PC/phone lifted the pause"
        await c.change_code()
        assert c.receiver.paused, "a new code lifted the pause"
        c.set_paused(False)
        await c.change_toy_source("ws://127.0.0.1:1")
        assert not c.receiver.paused
        await c.stop_share()
    asyncio.run(run())


def test_toy_reconnect_resends():
    async def run():
        toy = Intiface("ws://x", lambda m: None)
        toy.ws = FakeWs()
        lush = {"DeviceIndex": 0, "DeviceName": "Lovense Lush 3",
                "DeviceMessages": {"ScalarCmd": [{"ActuatorType": "Vibrate"}]}}
        toy.handle({"DeviceAdded": lush})
        await toy.set_level(0.6)
        toy.handle({"DeviceRemoved": {"DeviceIndex": 0}})
        toy.handle({"DeviceAdded": lush})
        toy.ws.sent.clear()
        await toy.set_level(0.6)
        assert toy.ws.sent and toy.ws.sent[0]["ScalarCmd"]["Scalars"][0]["Scalar"] == 0.6, "toy came back silent"
    asyncio.run(run())


def test_vrchat_closing_stops_input():
    import tempfile
    from vibevrc import core as core_mod, settings

    async def run():
        procs = {"vrchat.exe", "explorer.exe"}
        broken = [False]
        real = core_mod.open_process, core_mod.exited, core_mod.close_process, core_mod.running_processes
        core_mod.open_process = lambda name: 7 if name in procs else None
        core_mod.exited = lambda handle: broken[0] or "vrchat.exe" not in procs
        core_mod.close_process = lambda handle: None
        core_mod.running_processes = lambda: set(procs)
        try:
            cfg = settings.Config(send_port=19099, haptics=True, targets=[("Friend", new_share_code())])
            c = core_mod.Core(cfg, tempfile.mkdtemp(prefix="vibevrc_vrc_"))
            c.relay = FakeRelay()
            c.controller = core_mod.Controller("me", c.relay, c.osc_out, c.log)
            for nick, code in cfg.targets:
                c.controller.targets.append(core_mod.Target(nick, code))
            watch = asyncio.ensure_future(c.watch_vrchat(0.05))
            c.on_param(core_mod.PARAM + "Active", True)
            c.on_any_osc(haptics.PREFIX + "VFH/Zone/Touch/Head/Others", 0.8)
            for _ in range(10):
                c.haptics.tick()
                await asyncio.sleep(0.02)
            assert c.controller.desired(0)[0] and c.haptics.level > 0.5
            broken[0] = True
            await asyncio.sleep(0.3)
            assert c.controller.desired(0)[0], "a handle that can't be waited on isn't taken as VRChat closing"
            broken[0] = False
            procs.discard("vrchat.exe")
            await asyncio.sleep(0.3)
            for _ in range(10):
                c.haptics.tick()
                await asyncio.sleep(0.02)
            assert not c.controller.desired(0)[0], "partner's toy kept running after VRChat closed"
            assert c.haptics.level == 0.0, "own toy kept buzzing after VRChat closed"
            watch.cancel()
        finally:
            core_mod.open_process, core_mod.exited, core_mod.close_process, core_mod.running_processes = real
    asyncio.run(run())


def test_central_takeover_stops_toy():
    from vibevrc import engine

    class Proc:
        pid = 0

        def poll(self):
            return None

        def terminate(self):
            order.append("kill")

        def wait(self, timeout=None):
            return 0

    async def stop_toys():
        order.append("stop")

    async def run():
        sup = engine.EngineSupervisor("ws://127.0.0.1:12345", "x.exe", os.devnull, lambda m: None, lambda: True)
        sup.stop_toys = stop_toys
        sup.proc = Proc()
        task = asyncio.ensure_future(sup.run())
        await asyncio.sleep(0.3)
        task.cancel()

    order = []
    real = engine.running_processes
    engine.running_processes = lambda: {"intiface_central.exe"}
    try:
        asyncio.run(run())
    finally:
        engine.running_processes = real
    assert order[:2] == ["stop", "kill"], f"engine killed before the toy was stopped: {order}"


def test_stroke_only_toys_follow_level():
    async def run():
        toy = Intiface("ws://x", lambda m: None)
        toy.ws = FakeWs()
        toy.add({"DeviceIndex": 0, "DeviceName": "Kiiroo Keon",
                 "DeviceMessages": {"LinearCmd": [{"ActuatorType": "Position"}]}})
        sent = toy.ws.sent
        await toy.set_depth(None)
        await toy.set_level(0.5)
        assert sent and "LinearCmd" in sent[0], "a partner's level moves a stroke-only toy"
        first = sent[0]["LinearCmd"]["Vectors"][0]
        sent.clear()
        await toy.set_level(0.5)
        assert not sent, "waits for the stroke to finish"
        toy.devices[0]["next"] = 0.0
        await toy.set_level(0.5)
        second = sent[0]["LinearCmd"]["Vectors"][0]
        assert {first["Position"], second["Position"]} == {0.0, 1.0}, "strokes back and forth"
        toy.devices[0]["next"] = 0.0
        sent.clear()
        await toy.set_level(1.0)
        fast = sent[0]["LinearCmd"]["Vectors"][0]["Duration"]
        assert fast < second["Duration"], "faster at a higher level"
        toy.devices[0]["next"] = 0.0
        sent.clear()
        await toy.set_level(0.0)
        assert not sent, "level 0 stops issuing strokes"
        await toy.set_level(0.3)
        assert sent, "starts again straight away"
        sent.clear()
        await toy.set_depth(0.4)
        await toy.set_level(0.5)
        assert [m["LinearCmd"]["Vectors"][0]["Position"] for m in sent] == [0.4], "depth takes over"
    asyncio.run(run())


def test_test_respects_stop_sending():
    from vibevrc.controller import OFF, Controller, Target
    c = Controller("me", Stub(), Stub(), lambda m: None)
    c.targets.append(Target("Friend", new_share_code()))
    c.targets[0].test_until = time.monotonic() + 3
    assert c.desired(0)[0]
    c.set_panic(True)
    assert c.desired(0) == OFF, "Test still buzzed them with Stop sending on"


def test_control_own_toy_locally():
    import tempfile
    from vibevrc import core as core_mod, settings

    async def run():
        code = new_share_code()
        home = tempfile.mkdtemp(prefix="vibevrc_me_")
        with open(os.path.join(home, "share_code.txt"), "w") as f:
            f.write(code)
        cfg = settings.Config(send_port=19099, intiface_url="ws://127.0.0.1:1", targets=[("Me", code)])
        c = core_mod.Core(cfg, home)
        c.engine_path = ""
        await c.start(listen=False)
        me = c.controller.targets[0]
        assert c.receiver, "your own code in People connects your toy, even with sharing off"
        c.controller.osc_param("Intensity", 0.7)
        c.controller.osc_param("Active", True)
        await asyncio.sleep(0.5)
        assert c.receiver.controllers, "commands reach your own toy"
        assert me.ch.cmd_topic not in c.relay.topics, "and don't go through the relay"
        assert me.online, "shows as online"
        c.controller.osc_param("Active", False)
        await asyncio.sleep(0.3)
        assert not c.receiver.controllers
        await c.stop()

    real = core_mod.Relay
    core_mod.Relay = FakeRelay
    try:
        asyncio.run(run())
    finally:
        core_mod.Relay = real


def test_max_limits_follow_depth():
    class Toys:
        toy_names = []
        depths = []

        async def set_depth(self, d):
            self.depths.append(d)

        async def set_level(self, v):
            pass

    async def run():
        toys = Toys()
        r = Receiver(Channel(new_share_code()), toys, Stub(), Stub(), 0.5, 3, lambda m: None)
        r.remote = False
        r.depth = lambda: 0.8
        task = asyncio.ensure_future(r.run())
        await asyncio.sleep(0.1)
        r.depth = lambda: None
        await asyncio.sleep(0.3)
        task.cancel()
        assert toys.depths[0] == 0.4, f"Max 50% should halve the stroke, got {toys.depths[0]}"
        assert toys.depths[-1] is None
    asyncio.run(run())


def test_never_disables_other_haptics_apps():
    import tempfile
    from vibevrc import core as core_mod, settings

    class Osc:
        def __init__(self):
            self.sent = []

        def send_message(self, addr, value):
            self.sent.append((addr, value))

    async def run():
        cfg = settings.Config(send_port=19099, intiface_url="ws://127.0.0.1:1")
        c = core_mod.Core(cfg, tempfile.mkdtemp(prefix="vibevrc_ogb_"))
        c.engine_path = ""
        c.osc_out = Osc()
        await c.start(listen=False)
        await c.set_haptics(haptics=True)
        await c.set_haptics(haptics=False)
        await c.stop()
        enable = [v for a, v in c.osc_out.sent if a == core_mod.ENABLE]
        assert True in enable and False not in enable, f"OGB_ENABLED false turns off OSC Toys/OGB too: {enable}"

    real = core_mod.Relay
    core_mod.Relay = FakeRelay
    try:
        asyncio.run(run())
    finally:
        core_mod.Relay = real


def test_port_taken_by_other_app():
    from vibevrc import engine
    logs = []

    async def run():
        sup = engine.EngineSupervisor("ws://127.0.0.1:12345", "x.exe", os.devnull, logs.append, lambda: False)
        task = asyncio.ensure_future(sup.run())
        await asyncio.sleep(0.6)
        task.cancel()
        return sup.mode

    real = engine.port_open, engine.running_processes, engine.BUSY_AFTER, engine.POLL
    engine.port_open = lambda h, p: True
    engine.running_processes = lambda: set()
    engine.BUSY_AFTER, engine.POLL = 0.2, 0.05
    try:
        mode = asyncio.run(run())
    finally:
        engine.port_open, engine.running_processes, engine.BUSY_AFTER, engine.POLL = real
    assert mode == "busy" and any("port 12345" in l for l in logs), (mode, logs)


def test_length_ignores_mismatched_pairs():
    length = haptics.Length()
    for dist in (0.6, 0.55, 0.5, 0.45, 0.4, 0.35):
        length.update(1 - dist, 1 - (dist - 0.15))
    for _ in range(2):
        length.update(1 - 0.30, 1 - (0.3333 - 0.15))
    assert abs(length.value() - 0.15) < 0.001, f"two mismatched root/tip pairs beat six good ones: {length.value()}"


def test_length_with_interleaved_ticks():
    h = haptics.Haptics()
    socket = haptics.PREFIX + "OGB/Orf/Pussy/"
    steps = [0.6 - 0.0333 * k for k in range(13)] + [0.05] * 10
    for k, dist in enumerate(steps):
        t = 0.04 * k
        h.on_osc(socket + "PenOthersNewRoot", 1 - dist, t)
        h.tick(t + 0.001)
        h.on_osc(socket + "PenOthersNewTip", min(1.0, 1 - (dist - 0.15)), t + 0.002)
        if k % 3 == 0:
            h.tick(t + 0.003)
    zone = h.zones["Orf/Pussy"]
    assert abs(zone.depth("Others") - (1 - 0.05 / 0.15)) < 0.02, \
        f"a tick between root and tip paired values from different frames: depth {zone.depth('Others'):.3f}"


def test_stop_waits_for_engine():
    async def run():
        toy = Intiface("ws://x", lambda m: None)
        ws = toy.ws = FakeWs()
        toy.add({"DeviceIndex": 0, "DeviceName": "Lovense Lush 3",
                 "DeviceMessages": {"ScalarCmd": [{"ActuatorType": "Vibrate"}]}})
        stop = asyncio.ensure_future(toy.stop_all())
        await asyncio.sleep(0.05)
        assert not stop.done(), "the engine gets killed right after, so wait until it says the stop went out"
        await toy.set_level(0.8)
        assert [list(m)[0] for m in ws.sent] == ["StopAllDevices"], "nothing can start the toy again mid-stop"
        mid = ws.sent[-1]["StopAllDevices"]["Id"]
        toy.handle({"Ok": {"Id": mid}})
        await asyncio.wait_for(stop, 1)
        toy.ws = FakeWs()
        t = time.monotonic()
        await toy.stop_all()
        assert time.monotonic() - t < 1.5, "a silent engine doesn't hang shutdown"
    asyncio.run(run())


def test_rotating_toys():
    async def run():
        toy = Intiface("ws://x", lambda m: None)
        toy.ws = FakeWs()
        toy.add({"DeviceIndex": 0, "DeviceName": "Lovense Nora",
                 "DeviceMessages": {"ScalarCmd": [{"ActuatorType": "Vibrate"}, {"ActuatorType": "Rotate"}]}})
        await toy.set_level(0.5)
        scalars = toy.ws.sent[-1]["ScalarCmd"]["Scalars"]
        assert [s["ActuatorType"] for s in scalars] == ["Vibrate", "Rotate"], "the Nora rotates too"
        toy.add({"DeviceIndex": 1, "DeviceName": "Lovense Flexer",
                 "DeviceMessages": {"ScalarCmd": [{"ActuatorType": "Rotate"}]}})
        assert 1 in toy.devices, "a toy that only rotates is kept"
    asyncio.run(run())


def test_rescan_restarts_scanning():
    real = asyncio.sleep

    async def quick(seconds, *a):
        await real(0)

    async def run():
        toy = Intiface("ws://x", lambda m: None)
        ws = FakeWs()
        asyncio.sleep = quick
        try:
            task = asyncio.ensure_future(toy.rescan(ws))
            for _ in range(20):
                await real(0)
            task.cancel()
        finally:
            asyncio.sleep = real
        kinds = [list(m)[0] for m in ws.sent]
        assert kinds[:4] == ["StopScanning", "StartScanning"] * 2, f"Intiface ignores a second StartScanning: {kinds}"
    asyncio.run(run())


def test_level_refresh_and_readd():
    async def run():
        toy = Intiface("ws://x", lambda m: None)
        toy.ws = FakeWs()
        lush = {"DeviceIndex": 0, "DeviceName": "Lovense Lush 3",
                "DeviceMessages": {"ScalarCmd": [{"ActuatorType": "Vibrate"}]}}
        toy.handle({"DeviceAdded": lush})
        sent = toy.ws.sent
        await toy.set_level(0.6)
        await toy.set_level(0.6)
        assert len(sent) == 1, "a steady level isn't sent over and over"
        toy.devices[0]["at"] -= 1.1
        await toy.set_level(0.6)
        assert len(sent) == 2, "but it's refreshed every second in case the toy came back quietly"
        await toy.set_level(0.0)
        toy.handle({"DeviceRemoved": {"DeviceIndex": 0}})
        toy.handle({"DeviceAdded": lush})
        sent.clear()
        await toy.set_level(0.0)
        assert sent and sent[0]["ScalarCmd"]["Scalars"][0]["Scalar"] == 0, "a toy that comes back is told to stop"
    asyncio.run(run())


def test_ping_out_reconnects():
    logs = []
    toy = Intiface("ws://x", logs.append)
    toy.ws = FakeWs()
    try:
        toy.handle({"Error": {"Id": 0, "ErrorMessage": '{"ButtplugPingError":"PingedOut"}', "ErrorCode": 2}})
        raise AssertionError("a ping out has to end the session so VibeVRC reconnects")
    except ConnectionError:
        pass
    assert toy.ws is None and any("too slow" in l for l in logs)
    for _ in range(30):
        toy.handle({"Error": {"Id": 5, "ErrorMessage": "Device not available", "ErrorCode": 4}})
    assert sum("Device not available" in l for l in logs) == 1, "a repeated error is logged once"


class PubRelay(FakeRelay):
    def __init__(self, *a):
        super().__init__(*a)
        self.qos = []

    def publish(self, topic, *a, **k):
        self.qos.append(k.get("qos", 1))
        return super().publish(topic, *a, **k)


def test_commands_are_not_queued_offline():
    from vibevrc.controller import Controller, Target
    relay = PubRelay()
    c = Controller("me", relay, Stub(), lambda m: None)
    c.targets.append(Target("Friend", new_share_code()))
    c.send(c.targets[0], (True, 0.5, 0))
    assert relay.qos == [0], "QoS 0, so a dropped relay doesn't replay old commands later"
    from vibevrc.relay import Relay

    async def run():
        r = Relay("127.0.0.1", 1, False, lambda m: None)
        got = []
        r.client.publish = lambda topic, payload, qos, retain: got.append(qos)
        r.publish("t", b"x", qos=0)
        r.publish("t", b"x", retain=True)
        assert got == [0, 1]
    asyncio.run(run())


def test_status_ignores_other_messages():
    from vibevrc.controller import Controller, Target
    c = Controller("me", Stub(), Stub(), lambda m: None)
    t = Target("Friend", new_share_code())
    c.targets.append(t)
    c.on_status(t, t.ch.seal({"online": True, "toys": ["Lush\nfake log line"], "paused": False}))
    assert t.online and t.toys == ["Lushfake log line"], t.toys
    c.on_status(t, t.ch.seal({"sid": "x", "seq": 1, "ts": time.time(), "on": False}))
    assert t.online, "a command replayed on the status topic doesn't mark them offline"


def test_remove_keeps_menu_on_the_same_person():
    import tempfile
    from vibevrc import core as core_mod, settings
    from vibevrc.controller import OFF

    class Osc:
        def __init__(self):
            self.sent = []

        def send_message(self, addr, value):
            self.sent.append((addr, value))

    async def run():
        codes = [new_share_code() for _ in range(3)]
        cfg = settings.Config(send_port=19099, targets=[("A", codes[0]), ("B", codes[1]), ("C", codes[2])])
        c = core_mod.Core(cfg, tempfile.mkdtemp(prefix="vibevrc_rm_"))
        c.engine_path = ""
        c.osc_out = Osc()
        await c.start(listen=False)
        ctl = c.controller
        ctl.osc = c.osc_out
        ctl.osc_param("Target", 1)
        ctl.osc_param("Active", True)
        c.remove_target(0)
        assert ctl.targets[ctl.target].nick == "B", "removing someone above keeps the menu on the same person"
        assert (core_mod.PARAM + "Target", 0) in c.osc_out.sent, "and the avatar menu follows"
        c.remove_target(0)
        assert ctl.target == 255 and ctl.desired(0) == OFF, "removing the one you picked doesn't buzz someone else"
        await c.stop()

    real = core_mod.Relay
    core_mod.Relay = FakeRelay
    try:
        asyncio.run(run())
    finally:
        core_mod.Relay = real


def test_logging_never_breaks_the_toy():
    import tempfile
    from vibevrc import core as core_mod, settings

    class Full:
        def write(self, s):
            raise OSError(28, "No space left on device")

        def close(self):
            pass

    c = core_mod.Core(settings.Config(send_port=19099), tempfile.mkdtemp(prefix="vibevrc_log_"))
    c.log("a lone surrogate \ud800 is fine")
    assert c.logf is not None, "odd characters don't stop the log file"
    c.logf = Full()
    c.log("disk full")
    assert c.logf is None and c.lines[-1].endswith("disk full"), "a full disk doesn't stop VibeVRC"
    r = Receiver(Channel(new_share_code()), Stub(), Stub(), Stub(), 1.0, 3, c.log)
    r.take({"sid": "s", "seq": 1, "ts": time.time(), "name": "Ev\nil\ud800", "on": True, "i": 0.5, "p": 0})
    assert r.controllers["s"]["name"] == "Evil" and "seen" in r.controllers["s"]


def test_toy_loop_survives_errors():
    class Toys:
        toy_names = []

        def __init__(self):
            self.fail = 1
            self.levels = []

        async def set_depth(self, d):
            pass

        async def set_level(self, v):
            if self.fail:
                self.fail -= 1
                raise RuntimeError("boom")
            self.levels.append(v)

    async def run():
        logs = []
        toys = Toys()
        r = Receiver(Channel(new_share_code()), toys, Stub(), Stub(), 1.0, 3, logs.append)
        r.remote = False
        r.take({"sid": "s", "seq": 1, "ts": time.time(), "name": "A", "on": True, "i": 0.8, "p": 0})
        task = asyncio.ensure_future(r.run())
        await asyncio.sleep(0.6)
        assert not task.done(), "the toy loop keeps running after an error"
        task.cancel()
        assert toys.levels and set(toys.levels) == {0.0}, f"an error stops the toy: {toys.levels[:5]}"
        assert any("problem" in l for l in logs)
    asyncio.run(run())


def test_bad_settings_load_safely():
    import json
    import tempfile
    from vibevrc import settings
    home = tempfile.mkdtemp(prefix="vibevrc_cfg_")
    path = os.path.join(home, "settings.json")
    code = new_share_code()
    with open(path, "w") as f:
        json.dump({"targets": [["Friend", code], ["bad"], "x", ["Dup", code], ["Odd", "VV-nope"]], "max_intensity": "x",
                   "osc_port": 70000, "send_port": True, "haptics_off": None, "intiface_url": "ws://x:99999",
                   "send_ip": "   ", "haptics_strength": 2, "name": "  Me  ", "unknown": 1}, f)
    cfg = settings.load(home)
    assert cfg.targets == [("Friend", code)], cfg.targets
    assert (cfg.max_intensity, cfg.osc_port, cfg.send_port, cfg.haptics_off) == (1.0, 0, 9000, [])
    assert (cfg.intiface_url, cfg.send_ip, cfg.haptics_strength, cfg.name) == \
        ("ws://127.0.0.1:12345", "127.0.0.1", 1.0, "Me")
    with open(path, "w") as f:
        f.write("{broken")
    cfg = settings.load(home)
    assert cfg.targets == [] and os.path.exists(path + ".broken"), "a broken file is kept, not overwritten"
    with open(path, "w") as f:
        f.write("[1, 2]")
    assert settings.load(home).targets == []


def test_replays_blocked_after_rebuild():
    import tempfile
    from vibevrc import core as core_mod, settings

    async def run():
        cfg = settings.Config(share=True, send_port=19099, intiface_url="ws://127.0.0.1:1")
        c = core_mod.Core(cfg, tempfile.mkdtemp(prefix="vibevrc_replay_"))
        c.engine_path = ""
        c.relay = FakeRelay()
        c.prepare_share()
        c.run_share()
        ch = c.receiver.ch
        blob = ch.seal({"sid": "p", "seq": 3, "ts": time.time(), "name": "P", "on": True, "i": 0.5, "p": 0})
        c.receiver.on_cmd(blob)
        c.receiver.on_cmd(ch.seal({"sid": "p", "seq": 4, "ts": time.time(), "name": "P", "on": False}))
        await c.change_toy_source("ws://127.0.0.1:2")
        c.receiver.on_cmd(blob)
        assert not c.receiver.controllers, "a recorded command can't be replayed after the toy restarts"
        await c.stop_share()
    asyncio.run(run())


def test_pause_is_remembered():
    import tempfile
    from vibevrc import core as core_mod, settings

    async def run():
        home = tempfile.mkdtemp(prefix="vibevrc_pause2_")
        cfg = settings.Config(share=True, send_port=19099, intiface_url="ws://127.0.0.1:1")
        c = core_mod.Core(cfg, home)
        c.engine_path = ""
        c.relay = FakeRelay()
        c.prepare_share()
        c.set_paused(True)
        await asyncio.sleep(1.3)
        cfg2 = settings.load(home)
        assert cfg2.paused, "Pause is saved"
        c2 = core_mod.Core(cfg2, home)
        c2.relay = FakeRelay()
        c2.prepare_share()
        assert c2.receiver.paused, "and still on after a restart or an update"
    asyncio.run(run())


def test_block_param_edges():
    import tempfile
    from vibevrc import core as core_mod, settings

    async def run():
        cfg = settings.Config(share=True, send_port=19099, intiface_url="ws://127.0.0.1:1")
        c = core_mod.Core(cfg, tempfile.mkdtemp(prefix="vibevrc_block_"))
        c.engine_path = ""
        c.relay = FakeRelay()
        c.controller = core_mod.Controller("me", c.relay, c.osc_out, c.log)
        c.prepare_share()
        block = lambda on: c.on_param(core_mod.PARAM + "Block", on)
        c.set_paused(True)
        block(False)
        assert c.receiver.paused, "an avatar loading with Block off doesn't lift Pause"
        block(True)
        block(False)
        assert c.receiver.paused, "Block only lifts a pause it made"
        c.set_paused(False)
        block(True)
        assert c.receiver.paused
        block(False)
        assert not c.receiver.paused, "turning Block off lifts the pause it made"
        block(True)
        c.set_paused(False)
        block(True)
        assert c.receiver.paused, "an avatar loading with Block on pauses again"
    asyncio.run(run())


def test_sealed_size_is_fixed():
    import json
    ch = Channel(new_share_code())
    sizes = {len(ch.seal({"online": True, "toys": t, "paused": p})) for t in ([], ["Lovense Lush 3"]) for p in (True, False)}
    assert len(sizes) == 1, f"the size gives away pause state or toys: {sizes}"
    nonce = os.urandom(12)
    old = nonce + ch.aead.encrypt(nonce, json.dumps({"online": True}).encode(), ch.id.encode())
    assert ch.open(old) == {"online": True}, "messages from older versions still open"
    assert ch.open(ch.seal({"x": "a" * 20000})) is None, "huge messages are dropped before decrypting"


def test_clean():
    from vibevrc.controller import clean
    assert clean("A\nB\ud800\u200bC") == "ABC"
    assert clean("x" * 50) == "x" * 32


def test_osc_fast_path():
    from pythonosc.osc_bundle_builder import IMMEDIATELY, OscBundleBuilder
    from pythonosc.osc_message_builder import OscMessageBuilder
    from vibevrc.core import OscIn

    class Core:
        def __init__(self):
            self.got = []
            self.heard_n = 0

        def heard(self):
            self.heard_n += 1

        def on_osc(self, address, vals):
            self.got.append((address, vals))

        def log(self, m):
            pass

    def msg(addr, value):
        b = OscMessageBuilder(addr)
        b.add_arg(value)
        return b.build()

    core = Core()
    p = OscIn(core)
    p.datagram_received(msg("/avatar/parameters/VelocityX", 0.5).dgram, None)
    p.datagram_received(msg("/avatar/parameters/FT/v2/EyeLeftX", 0.1).dgram, None)
    assert core.heard_n == 2 and not core.got, "face tracking and movement are skipped without parsing"
    keep = ["/avatar/parameters/VibeVRC/Active", "/avatar/parameters/OGB/Orf/Pussy/PenOthers",
            "/avatar/parameters/VFH/Zone/Touch/Head/Others", "/avatar/change"]
    for a in keep:
        p.datagram_received(msg(a, "avtr_x" if a == "/avatar/change" else 1.0).dgram, None)
    assert [a for a, _ in core.got] == keep, core.got
    b = OscBundleBuilder(IMMEDIATELY)
    b.add_content(msg("/avatar/parameters/VibeVRC/Intensity", 0.5))
    p.datagram_received(b.build().dgram, None)
    assert core.got[-1] == ("/avatar/parameters/VibeVRC/Intensity", [0.5])
    p.datagram_received(b"garbage", None)


def test_relay_restart_never_blocks():
    import socket
    import threading
    from vibevrc.relay import Relay

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    port = srv.getsockname()[1]
    conns = []

    def accept():
        try:
            while True:
                conns.append(srv.accept()[0])
        except OSError:
            pass

    threading.Thread(target=accept, daemon=True).start()

    async def run():
        r = Relay("127.0.0.1", port, True, lambda m: None)
        r.start()
        await asyncio.sleep(0.5)
        t = time.monotonic()
        r.restart()
        r.restart()
        assert time.monotonic() - t < 0.5, "a stuck relay connection doesn't freeze the toy loop"
        t = time.monotonic()
        r.stop()
        assert time.monotonic() - t < 2.5

    try:
        asyncio.run(run())
    finally:
        srv.close()
        for c in conns:
            c.close()


def test_process_helpers():
    import ctypes
    import subprocess
    from vibevrc import engine
    assert os.path.basename(sys.executable).lower() in engine.running_processes()
    assert engine.open_process("no-such-thing.exe") is None
    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = ctypes.c_void_p
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.6)"])
    h = k32.OpenProcess(engine.SYNCHRONIZE, False, p.pid)
    assert h and not engine.exited(h)
    p.wait()
    assert engine.exited(h)
    engine.close_process(h)
    engine.keep_awake(True)
    engine.keep_awake(False)


if __name__ == "__main__":
    test_codes()
    test_seal()
    test_replay_and_clock()
    test_phone_url()
    test_patterns()
    test_finder_networks()
    test_finder_search()
    test_haptics_depth()
    test_haptics_motion()
    test_haptics_strength()
    test_updater()
    test_haptics_seed()
    test_haptics_stroke()
    test_intiface_depth()
    test_stop_waits_for_engine()
    test_haptics_close_gate()
    test_pause_survives_rebuild()
    test_toy_reconnect_resends()
    test_vrchat_closing_stops_input()
    test_central_takeover_stops_toy()
    test_stroke_only_toys_follow_level()
    test_test_respects_stop_sending()
    test_control_own_toy_locally()
    test_max_limits_follow_depth()
    test_never_disables_other_haptics_apps()
    test_port_taken_by_other_app()
    test_clock_warning()
    test_length_ignores_mismatched_pairs()
    test_length_with_interleaved_ticks()
    test_rotating_toys()
    test_rescan_restarts_scanning()
    test_level_refresh_and_readd()
    test_ping_out_reconnects()
    test_commands_are_not_queued_offline()
    test_status_ignores_other_messages()
    test_remove_keeps_menu_on_the_same_person()
    test_logging_never_breaks_the_toy()
    test_toy_loop_survives_errors()
    test_bad_settings_load_safely()
    test_replays_blocked_after_rebuild()
    test_pause_is_remembered()
    test_block_param_edges()
    test_sealed_size_is_fixed()
    test_clean()
    test_osc_fast_path()
    test_relay_restart_never_blocks()
    test_process_helpers()
    print("ok")

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
        real = core_mod.running_processes
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
            procs.discard("vrchat.exe")
            await asyncio.sleep(0.3)
            for _ in range(10):
                c.haptics.tick()
                await asyncio.sleep(0.02)
            assert not c.controller.desired(0)[0], "partner's toy kept running after VRChat closed"
            assert c.haptics.level == 0.0, "own toy kept buzzing after VRChat closed"
            watch.cancel()
        finally:
            core_mod.running_processes = real
    asyncio.run(run())


def test_central_takeover_stops_toy():
    from vibevrc import engine

    class Proc:
        pid = 0

        def poll(self):
            return None

        def terminate(self):
            order.append("kill")

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


def test_stop_waits_for_engine():
    async def run():
        toy = Intiface("ws://x", lambda m: None)
        toy.ws = FakeWs()
        stop = asyncio.ensure_future(toy.stop_all())
        await asyncio.sleep(0.05)
        assert not stop.done(), "the engine gets killed right after, so wait until it says the stop went out"
        mid = toy.ws.sent[-1]["StopAllDevices"]["Id"]
        toy.handle({"Ok": {"Id": mid}})
        await asyncio.wait_for(stop, 1)
        toy.ws = FakeWs()
        t = time.monotonic()
        await toy.stop_all()
        assert time.monotonic() - t < 1.5, "a silent engine doesn't hang shutdown"
    asyncio.run(run())


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
    print("ok")

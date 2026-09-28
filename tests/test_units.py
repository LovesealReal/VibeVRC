import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vibevrc import finder, haptics, patterns
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
    assert "s2" not in r.controllers and any("clock" in l for l in logs)
    r.on_cmd(ch.seal({**msg, "sid": "s3", "i": 9, "p": 99}))
    assert r.controllers["s3"]["i"] == 1.0 and r.controllers["s3"]["p"] == 0


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
    print("ok")

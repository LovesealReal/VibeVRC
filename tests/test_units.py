import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vibevrc import patterns
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


if __name__ == "__main__":
    test_codes()
    test_seal()
    test_replay_and_clock()
    test_phone_url()
    test_patterns()
    print("ok")

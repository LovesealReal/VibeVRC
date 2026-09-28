import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import AsyncIOOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient

from mock_intiface import MockIntiface
from vibevrc.relay import new_share_code

P = "/avatar/parameters/VibeVRC/"
MOCK_PORT = 12399
CAP = 0.8

OWNER = {"osc_port": 19001, "send_port": 19000, "share": True, "setup_done": True,
         "intiface_url": f"ws://127.0.0.1:{MOCK_PORT}", "max_intensity": CAP, "timeout": 3}
CONTROLLER = {"osc_port": 19011, "send_port": 19010, "name": "Tester", "setup_done": True}


def make_home(tmp, name, data):
    home = os.path.join(tmp, name)
    os.makedirs(home)
    with open(os.path.join(home, "settings.json"), "w") as f:
        json.dump(data, f)
    return home


class Proc:
    def __init__(self, name, home):
        self.name = name
        self.lines = []
        exe = os.environ.get("VIBEVRC_EXE")
        cmd = [exe] if exe else [sys.executable, "-u", "-m", "vibevrc.cli"]
        self.p = subprocess.Popen(cmd + ["--home", home],
                                  cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  stdin=subprocess.DEVNULL, text=True, encoding="utf-8")
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.p.stdout:
            line = line.rstrip()
            self.lines.append(line)
            print(f"  [{self.name}] {line}")

    def kill(self):
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.p.pid)], capture_output=True)

    def saw(self, text):
        return any(text in l for l in self.lines)


async def wait_for(cond, timeout, what):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"timed out waiting for: {what}")


async def main():
    mock = MockIntiface()
    await mock.start(MOCK_PORT)

    osc_seen = {}
    disp = Dispatcher()
    disp.map(P + "*", lambda a, *v: osc_seen.__setitem__(a[len(P):], v[0]))
    loop = asyncio.get_running_loop()
    t1, _ = await AsyncIOOSCUDPServer(("127.0.0.1", 19010), disp, loop).create_serve_endpoint()
    t2, _ = await AsyncIOOSCUDPServer(("127.0.0.1", 19000), disp, loop).create_serve_endpoint()

    tmp = tempfile.mkdtemp(prefix="vibevrc_e2e_")
    code = new_share_code()
    owner_home = make_home(tmp, "owner", OWNER)
    with open(os.path.join(owner_home, "share_code.txt"), "w") as f:
        f.write(code)
    ctl_home = make_home(tmp, "ctrl", dict(CONTROLLER, targets=[["Friend", code]]))

    vrc = SimpleUDPClient("127.0.0.1", 19011)
    owner = SimpleUDPClient("127.0.0.1", 19001)
    lush = lambda: mock.levels.get(0, 0.0)
    near = lambda v, tol=0.02: (lambda: abs(lush() - v) <= tol)

    rx = Proc("owner", owner_home)
    cx = None
    try:
        await wait_for(lambda: rx.saw("Toy connected: Lovense Edge 2"), 10, "receiver sees mock toys")
        assert rx.saw("doesn't vibrate"), "stroker should be ignored"
        assert mock.handshake["MessageVersion"] == 3
        await wait_for(lambda: rx.saw("Connected to the relay"), 15, "receiver on relay")

        cx = Proc("ctrl", ctl_home)
        await wait_for(lambda: cx.saw("Friend is online (Lovense Lush 3, Lovense Edge 2)"), 20, "controller sees target online")
        vrc.send_message(P + "Target", 0)
        await wait_for(lambda: osc_seen.get("TargetOnline") is True, 3, "TargetOnline feedback")

        vrc.send_message(P + "Intensity", 0.6)
        vrc.send_message(P + "Active", True)
        await wait_for(near(0.6 * CAP), 5, "steady 60% (capped)")
        await wait_for(lambda: abs(mock.levels.get(1, 0) - 0.6 * CAP) < 0.02, 2, "second toy follows")
        await wait_for(lambda: abs(osc_seen.get("Receiving", 0) - 0.6 * CAP) < 0.03, 2, "Receiving OSC to owner")

        vrc.send_message(P + "Intensity", 1.0)
        await wait_for(near(CAP), 5, "cap at max_intensity")

        vrc.send_message(P + "Pattern", 1)
        start = len(mock.history)
        await asyncio.sleep(3)
        vals = {round(v, 2) for _, d, v in mock.history[start:] if d == 0}
        assert 0.0 in vals and round(CAP, 2) in vals, vals
        vrc.send_message(P + "Pattern", 0)

        owner.send_message(P + "Block", True)
        await wait_for(near(0.0), 3, "owner Block stops toy")
        await asyncio.sleep(1.5)
        assert lush() == 0.0
        owner.send_message(P + "Block", False)
        await wait_for(near(CAP), 3, "unblock resumes")

        vrc.send_message(P + "Active", False)
        await wait_for(near(0.0), 3, "menu off stops toy")


        vrc.send_message(P + "Target", 3)
        vrc.send_message(P + "Active", True)
        await asyncio.sleep(1.5)
        assert lush() == 0.0
        vrc.send_message(P + "Target", 0)
        await wait_for(near(CAP), 3, "back on target 0")

        cx.kill()
        await wait_for(near(0.0), 6, "dead-man timeout stops toy")
        assert rx.saw("Lost Tester")
        print("ok")
    finally:
        for p in (rx, cx):
            if p and p.p.poll() is None:
                p.kill()
        t1.close()
        t2.close()


if __name__ == "__main__":
    asyncio.run(main())

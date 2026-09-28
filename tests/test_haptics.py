import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import AsyncIOOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient

from mock_intiface import MockIntiface
from test_e2e import Proc, make_home, wait_for
from vibevrc.relay import new_share_code

A = "/avatar/parameters/"
MOCK_PORT = 12398
PLUG = 0.15

OWNER = {"osc_port": 19021, "send_port": 19020, "share": True, "setup_done": True, "haptics": True,
         "intiface_url": f"ws://127.0.0.1:{MOCK_PORT}", "max_intensity": 1.0, "timeout": 3}
CONTROLLER = {"osc_port": 19031, "send_port": 19030, "name": "Tester", "setup_done": True, "haptics_send": True}


def insert(osc, socket, dist, party="Others"):
    tip = min(1.0, 1 - max(0.0, dist - PLUG))
    osc.send_message(f"{A}OGB/Orf/{socket}/Pen{party}NewRoot", max(0.0, 1 - dist))
    osc.send_message(f"{A}OGB/Orf/{socket}/Pen{party}NewTip", tip if dist < 1 + PLUG else 0.0)


async def slide(osc, socket, start, end, steps=12, party="Others"):
    for i in range(steps + 1):
        insert(osc, socket, start + (end - start) * i / steps, party)
        await asyncio.sleep(0.04)


async def main():
    mock = MockIntiface()
    await mock.start(MOCK_PORT)
    seen = {}
    disp = Dispatcher()
    disp.set_default_handler(lambda a, *v: seen.__setitem__(a, v[0] if v else None))
    loop = asyncio.get_running_loop()
    t1, _ = await AsyncIOOSCUDPServer(("127.0.0.1", 19020), disp, loop).create_serve_endpoint()
    t2, _ = await AsyncIOOSCUDPServer(("127.0.0.1", 19030), disp, loop).create_serve_endpoint()

    tmp = tempfile.mkdtemp(prefix="vibevrc_haptics_")
    code = new_share_code()
    owner_home = make_home(tmp, "owner", OWNER)
    with open(os.path.join(owner_home, "share_code.txt"), "w") as f:
        f.write(code)
    ctl_home = make_home(tmp, "ctrl", dict(CONTROLLER, targets=[["Friend", code]]))
    me = SimpleUDPClient("127.0.0.1", 19021)
    them = SimpleUDPClient("127.0.0.1", 19031)
    lush = lambda: mock.levels.get(0, 0.0)
    near = lambda v, tol=0.05: (lambda: abs(lush() - v) <= tol)

    rx = Proc("owner", owner_home)
    cx = None
    try:
        await wait_for(lambda: rx.saw("Toy connected: Lovense Edge 2"), 10, "toy connects")
        await wait_for(lambda: seen.get(A + "OGB_ENABLED") is True, 7, "OGB_ENABLED keepalive")

        await slide(me, "Pussy", 0.6, 0.2)
        await asyncio.sleep(0.5)
        assert lush() == 0.0, f"no buzz before entering, got {lush()}"
        await slide(me, "Pussy", 0.2, 0.05)
        depth = 1 - 0.05 / PLUG
        await wait_for(near((depth - 0.03) / 0.97), 3, "buzz follows depth")
        await slide(me, "Pussy", 0.05, 0.12, steps=6)
        await wait_for(near((1 - 0.12 / PLUG - 0.03) / 0.97), 3, "shallower is weaker")
        await slide(me, "Pussy", 0.12, 1.3, steps=8)
        await wait_for(near(0.0, 0.01), 3, "pulling out stops")

        await slide(me, "Pussy", 0.6, 0.05, party="Self")
        await asyncio.sleep(0.6)
        assert lush() == 0.0, "own plug ignored by default"
        await slide(me, "Pussy", 0.05, 1.3, steps=6, party="Self")

        me.send_message(A + "VFH/Zone/Touch/Head/Others", 0.5)
        await wait_for(near((0.5 - 0.03) / 0.97), 3, "touch zone")
        me.send_message(A + "VibeVRC/HapticsStrength", 1.0)
        await wait_for(near(min(1.0, 2 * (0.5 - 0.03) / 0.97)), 3, "menu strength")
        me.send_message(A + "VibeVRC/HapticsTouch", False)
        await wait_for(near(0.0, 0.01), 3, "menu turns touch off")
        me.send_message(A + "VibeVRC/HapticsTouch", True)
        await wait_for(lambda: lush() > 0.5, 3, "touch back on")
        seen.pop(A + "VibeVRC/Haptics", None)
        me.send_message("/avatar/change", "avtr_x")
        await wait_for(near(0.0, 0.01), 3, "avatar change clears")
        me.send_message(A + "VibeVRC/Haptics", False)
        await asyncio.sleep(0.5)
        assert not rx.saw("Your avatar stopped"), "saved menu values right after an avatar change are ignored"
        await wait_for(lambda: seen.get(A + "VibeVRC/Haptics") is True, 4, "app pushes its settings to the new avatar")
        await asyncio.sleep(0.3)

        me.send_message(A + "VibeVRC/Haptics", False)
        await wait_for(lambda: rx.saw("Your avatar stopped controlling your toy."), 3, "menu turns haptics off")
        await wait_for(lambda: seen.get(A + "OGB_ENABLED") is False, 3, "OGB_ENABLED cleared")
        me.send_message(A + "VFH/Zone/Touch/Head/Others", 0.9)
        await asyncio.sleep(0.6)
        assert lush() == 0.0, "haptics off means no buzz"
        me.send_message(A + "VFH/Zone/Touch/Head/Others", 0.0)

        await wait_for(lambda: rx.saw("Connected to the relay"), 15, "owner on relay")
        cx = Proc("ctrl", ctl_home)
        await wait_for(lambda: cx.saw("Friend is online"), 20, "controller sees owner")
        await wait_for(lambda: seen.get(A + "OGB_ENABLED") is True, 7, "controller enables haptics")
        them.send_message(A + "VFH/Zone/Touch/Chest/Others", 0.6)
        await wait_for(near((0.6 - 0.03) / 0.97), 6, "partner's touch drives the toy")
        assert cx.saw("Friend: sending your avatar's touches")
        them.send_message(A + "VFH/Zone/Touch/Chest/Others", 0.0)
        await wait_for(near(0.0, 0.01), 3, "partner touch ends")
        await wait_for(lambda: rx.saw("Tester stopped."), 8, "hold then stop")
        print("ok")
    finally:
        for p in (rx, cx):
            if p and p.p.poll() is None:
                p.kill()
        t1.close()
        t2.close()


if __name__ == "__main__":
    asyncio.run(main())

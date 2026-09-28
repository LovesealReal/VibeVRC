import asyncio
import itertools
import json

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

VIBE_TYPES = ("Vibrate", "Oscillate")


class Intiface:
    def __init__(self, url, log, remote=False):
        self.url = url
        self.log = log
        self.remote = remote
        self.ws = None
        self.devices = {}
        self.on_change = None
        self.ids = itertools.count(1)
        self.last = {}
        self.hinted = False

    @property
    def toy_names(self):
        return [d["name"] for d in self.devices.values()]

    async def run(self):
        while True:
            try:
                async with connect(self.url, open_timeout=5, ping_interval=None, proxy=None) as ws:
                    await self.session(ws)
                self.log("Intiface disconnected.")
            except (OSError, asyncio.TimeoutError, WebSocketException, RuntimeError, ValueError, KeyError):
                if self.ws is not None:
                    self.log("Lost Intiface. Reconnecting.")
            self.ws = None
            if self.devices:
                self.devices.clear()
                self.changed()
            self.last.clear()
            await asyncio.sleep(3)

    async def session(self, ws):
        await ws.send(self.msg("RequestServerInfo", ClientName="VibeVRC", MessageVersion=3))
        info = None
        for m in json.loads(await asyncio.wait_for(ws.recv(), 5)):
            if "Error" in m:
                raise RuntimeError(m["Error"].get("ErrorMessage"))
            info = m.get("ServerInfo", info)
        if info is None:
            raise RuntimeError("no ServerInfo from Intiface")
        self.ws = ws
        self.log(f"Connected to {info.get('ServerName', 'Intiface')}. Looking for your toy.")
        await ws.send(self.msg("StartScanning"))
        await ws.send(self.msg("RequestDeviceList"))

        helpers = [asyncio.create_task(self.rescan(ws))]
        ping_ms = info.get("MaxPingTime", 0)
        if ping_ms:
            helpers.append(asyncio.create_task(self.ping(ws, ping_ms / 2000)))
        try:
            async for raw in ws:
                for m in json.loads(raw):
                    self.handle(m)
        finally:
            for h in helpers:
                h.cancel()

    async def rescan(self, ws):
        await asyncio.sleep(20)
        while True:
            if not self.devices:
                if not self.hinted:
                    self.hinted = True
                    if self.remote:
                        self.log("No toy yet. Check that it shows up in Intiface Central on the phone.")
                    else:
                        self.log("No toy yet. Check that:\n"
                                 "  the toy is on and its light is blinking\n"
                                 "  Bluetooth is on (desktops may need a USB adapter)\n"
                                 "  Lovense Remote isn't connected to it\n"
                                 "  it's close to the PC")
                await ws.send(self.msg("StartScanning"))
            await asyncio.sleep(30)

    async def ping(self, ws, every):
        while True:
            await asyncio.sleep(every)
            await ws.send(self.msg("Ping"))

    def handle(self, m):
        if "DeviceList" in m:
            for d in m["DeviceList"].get("Devices", []):
                self.add(d)
        elif "DeviceAdded" in m:
            self.add(m["DeviceAdded"])
        elif "DeviceRemoved" in m:
            dev = self.devices.pop(m["DeviceRemoved"]["DeviceIndex"], None)
            if dev:
                self.log(f"Toy disconnected: {dev['name']}")
                self.changed()
        elif "Error" in m:
            self.log(f"Intiface error: {m['Error'].get('ErrorMessage')}")

    def add(self, d):
        idx = d["DeviceIndex"]
        if idx in self.devices:
            return
        scalars = d.get("DeviceMessages", {}).get("ScalarCmd", [])
        feats = [(i, s["ActuatorType"]) for i, s in enumerate(scalars) if s.get("ActuatorType") in VIBE_TYPES]
        name = d.get("DeviceDisplayName") or d.get("DeviceName", f"Device {idx}")
        if not feats:
            self.log(f"Skipping {name}, it doesn't vibrate.")
            return
        self.devices[idx] = {"name": name, "features": feats}
        self.log(f"Toy connected: {name} ({len(feats)} motor{'s' if len(feats) > 1 else ''})")
        self.changed()

    def changed(self):
        if self.on_change:
            self.on_change()

    def msg(self, kind, **fields):
        return json.dumps([{kind: {"Id": next(self.ids), **fields}}])

    async def set_level(self, level):
        if self.ws is None:
            return
        for idx, dev in list(self.devices.items()):
            last = self.last.get(idx, 0.0)
            if abs(level - last) < 0.005 and not (level == 0 and last != 0):
                continue
            scalars = [{"Index": i, "Scalar": round(level, 3), "ActuatorType": a} for i, a in dev["features"]]
            try:
                await self.ws.send(self.msg("ScalarCmd", DeviceIndex=idx, Scalars=scalars))
            except WebSocketException:
                return
            self.last[idx] = level

    async def stop_all(self):
        if self.ws is None:
            return
        try:
            await self.ws.send(self.msg("StopAllDevices"))
        except WebSocketException:
            pass
        self.last.clear()

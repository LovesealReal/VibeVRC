import asyncio
import json
import time

from websockets.asyncio.server import serve

DEVICES = [
    {"DeviceName": "Lovense Lush 3", "DeviceIndex": 0,
     "DeviceMessages": {"ScalarCmd": [{"StepCount": 20, "FeatureDescriptor": "", "ActuatorType": "Vibrate"}],
                        "StopDeviceCmd": {}}},
    {"DeviceName": "Lovense Edge 2", "DeviceIndex": 1,
     "DeviceMessages": {"ScalarCmd": [{"StepCount": 20, "FeatureDescriptor": "", "ActuatorType": "Vibrate"},
                                      {"StepCount": 20, "FeatureDescriptor": "", "ActuatorType": "Vibrate"}],
                        "StopDeviceCmd": {}}},
    {"DeviceName": "Fake Stroker", "DeviceIndex": 2,
     "DeviceMessages": {"LinearCmd": [{"StepCount": 100, "ActuatorType": "Position"}]}},
]


class MockIntiface:
    def __init__(self):
        self.levels = {}
        self.history = []
        self.handshake = None

    async def handler(self, ws):
        async for raw in ws:
            out = []
            for m in json.loads(raw):
                (kind, body), = m.items()
                mid = body.get("Id", 0)
                if kind == "RequestServerInfo":
                    self.handshake = body
                    out.append({"ServerInfo": {"Id": mid, "ServerName": "Mock Intiface",
                                               "MessageVersion": 3, "MaxPingTime": 0}})
                elif kind == "RequestDeviceList":
                    out.append({"DeviceList": {"Id": mid, "Devices": DEVICES}})
                elif kind == "ScalarCmd":
                    lv = [s["Scalar"] for s in body["Scalars"]]
                    assert all(s["ActuatorType"] == "Vibrate" for s in body["Scalars"])
                    self.levels[body["DeviceIndex"]] = lv[0]
                    self.history.append((time.monotonic(), body["DeviceIndex"], lv[0]))
                    out.append({"Ok": {"Id": mid}})
                elif kind == "StopAllDevices":
                    for d in self.levels:
                        self.levels[d] = 0.0
                    self.history.append((time.monotonic(), -1, 0.0))
                    out.append({"Ok": {"Id": mid}})
                else:
                    out.append({"Ok": {"Id": mid}})
            await ws.send(json.dumps(out))

    async def start(self, port, host="127.0.0.1"):
        self.server = await serve(self.handler, host, port)

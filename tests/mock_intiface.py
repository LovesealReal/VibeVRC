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
    {"DeviceName": "Fake Stroker", "DeviceIndex": 2, "DeviceMessageTimingGap": 100,
     "DeviceMessages": {"LinearCmd": [{"StepCount": 100, "FeatureDescriptor": "", "ActuatorType": "Position"}],
                        "ScalarCmd": [{"StepCount": 20, "FeatureDescriptor": "", "ActuatorType": "Oscillate"}],
                        "StopDeviceCmd": {}}},
    {"DeviceName": "Fake Keon", "DeviceIndex": 3, "DeviceMessageTimingGap": 100,
     "DeviceMessages": {"LinearCmd": [{"StepCount": 100, "FeatureDescriptor": "", "ActuatorType": "Position"}],
                        "StopDeviceCmd": {}}},
]


class MockIntiface:
    def __init__(self):
        self.levels = {}
        self.positions = {}
        self.history = []
        self.moves = []
        self.handshake = None

    def cancel(self, idx):
        moves = [m for m in self.moves if m[1] == idx]
        if moves and time.monotonic() - moves[-1][0] < 0.1:
            self.positions[idx] = moves[-2][2] if len(moves) > 1 else 0.0

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
                    assert all(s["ActuatorType"] in ("Vibrate", "Oscillate") for s in body["Scalars"])
                    self.levels[body["DeviceIndex"]] = lv[0]
                    self.history.append((time.monotonic(), body["DeviceIndex"], lv[0]))
                    if lv[0] == 0:
                        self.cancel(body["DeviceIndex"])
                    out.append({"Ok": {"Id": mid}})
                elif kind == "LinearCmd":
                    v = body["Vectors"][0]
                    self.positions[body["DeviceIndex"]] = v["Position"]
                    self.moves.append((time.monotonic(), body["DeviceIndex"], v["Position"], v["Duration"]))
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

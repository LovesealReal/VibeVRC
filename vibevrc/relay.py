import asyncio
import base64
import hashlib
import json
import os
import re
import secrets

import paho.mqtt.client as mqtt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

TOPIC_ROOT = "vibevrc/v1"
CODE_CHARS = re.compile(r"^[A-Z2-7]{24}$")


def format_code(body):
    return "VV-" + "-".join(body[i:i + 4] for i in range(0, 24, 4))


def new_share_code():
    return format_code(base64.b32encode(secrets.token_bytes(15)).decode())


def normalize_code(code):
    body = re.sub(r"[\s\-]", "", code.upper())
    if len(body) == 26 and body.startswith("VV"):
        body = body[2:]
    body = body.replace("0", "O").replace("1", "I").replace("8", "B")
    if not CODE_CHARS.match(body):
        raise ValueError(f"not a valid share code: {code!r}")
    return body


class Channel:
    def __init__(self, code):
        body = normalize_code(code).encode()
        self.id = hashlib.sha256(b"vibevrc-id|" + body).hexdigest()[:24]
        self.aead = AESGCM(hashlib.sha256(b"vibevrc-key|" + body).digest())
        self.cmd_topic = f"{TOPIC_ROOT}/{self.id}/cmd"
        self.status_topic = f"{TOPIC_ROOT}/{self.id}/status"

    def seal(self, obj):
        nonce = os.urandom(12)
        data = json.dumps(obj, separators=(",", ":")).encode()
        return nonce + self.aead.encrypt(nonce, data, self.id.encode())

    def open(self, blob):
        try:
            obj = json.loads(self.aead.decrypt(blob[:12], blob[12:], self.id.encode()))
        except Exception:
            return None
        return obj if isinstance(obj, dict) else None


class Relay:
    def __init__(self, host, port, tls, log):
        self.host = host
        self.port = port
        self.tls = tls
        self.log = log
        self.loop = asyncio.get_running_loop()
        self.connected = False
        self.on_connect = []
        self.handlers = {}
        self.will = None
        self.client = self.make_client()

    def make_client(self):
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="vibevrc-" + secrets.token_hex(8),
                        protocol=mqtt.MQTTv311)
        if self.tls:
            c.tls_set()
        c.reconnect_delay_set(1, 30)
        c.on_connect = self.connected_cb
        c.on_disconnect = self.disconnected_cb
        c.on_message = self.message_cb
        if self.will:
            c.will_set(*self.will, qos=1, retain=True)
        return c

    def set_will(self, topic, payload):
        self.will = (topic, payload)
        self.client.will_set(topic, payload, qos=1, retain=True)

    def clear_will(self):
        self.will = None
        self.client.will_clear()

    def subscribe(self, topic, callback):
        self.handlers[topic] = callback
        if self.connected:
            self.client.subscribe(topic, qos=1)

    def unsubscribe(self, topic):
        if self.handlers.pop(topic, None) and self.connected:
            self.client.unsubscribe(topic)

    def publish(self, topic, payload, retain=False):
        return self.client.publish(topic, payload, qos=1, retain=retain)

    def start(self):
        self.log("Connecting to the relay.")
        self.client.connect_async(self.host, self.port, keepalive=30)
        self.client.loop_start()

    def restart(self):
        old = self.client
        self.connected = False
        old.disconnect()
        old.loop_stop()
        self.client = self.make_client()
        self.client.connect_async(self.host, self.port, keepalive=30)
        self.client.loop_start()

    def stop(self):
        self.connected = False
        self.client.disconnect()
        self.client.loop_stop()

    def connected_cb(self, client, userdata, flags, reason_code, properties):
        if client is not self.client:
            return
        if reason_code.is_failure:
            self.log(f"Relay refused: {reason_code}")
            return
        self.connected = True
        self.log("Connected to the relay.")
        for topic in list(self.handlers):
            client.subscribe(topic, qos=1)
        for cb in self.on_connect:
            self.loop.call_soon_threadsafe(cb)

    def disconnected_cb(self, client, userdata, flags, reason_code, properties):
        if client is not self.client:
            return
        if self.connected:
            self.log("Lost the relay. Reconnecting.")
        self.connected = False

    def message_cb(self, client, userdata, msg):
        cb = self.handlers.get(msg.topic)
        if cb:
            self.loop.call_soon_threadsafe(cb, bytes(msg.payload))

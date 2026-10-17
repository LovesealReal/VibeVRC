import json
import os
import re
import secrets
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from zeroconf import ServiceInfo, Zeroconf

TREE = {
    "DESCRIPTION": "root node",
    "FULL_PATH": "/",
    "ACCESS": 0,
    "CONTENTS": {
        "avatar": {
            "FULL_PATH": "/avatar",
            "ACCESS": 2,
            "CONTENTS": {
                "change": {"FULL_PATH": "/avatar/change", "ACCESS": 2, "TYPE": "s"},
                "parameters": {"FULL_PATH": "/avatar/parameters", "ACCESS": 2},
            },
        }
    },
}


VRC_LOGS = os.path.join(os.path.expanduser("~"), "AppData", "LocalLow", "VRChat", "VRChat")
VRC_PORT = re.compile(r"of type OSCQuery on (\d+)")
found = {}


def find_node(path):
    node = TREE
    for part in [p for p in path.split("/") if p]:
        node = node.get("CONTENTS", {}).get(part)
        if node is None:
            return None
    return node


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path, _, query = self.path.partition("?")
        if query.startswith("HOST_INFO") or path.rstrip("/").endswith("HOST_INFO"):
            body = self.server.host_info
        else:
            body = find_node(path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class OscQuery:
    def __init__(self, osc_port):
        tag = secrets.token_hex(3).upper()
        self.name = "VibeVRC-" + tag
        self.tag = "OSC-" + tag
        self.osc_port = osc_port
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.http.daemon_threads = True
        self.http.host_info = {
            "NAME": self.name,
            "OSC_IP": "127.0.0.1",
            "OSC_PORT": osc_port,
            "OSC_TRANSPORT": "UDP",
            "EXTENSIONS": {"ACCESS": True, "CLIPMODE": False, "RANGE": True, "TYPE": True, "VALUE": True},
        }
        self.http_port = self.http.server_address[1]
        self.zc = None

    def start(self):
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.zc = Zeroconf()
        self.zc.register_service(ServiceInfo(
            "_oscjson._tcp.local.", f"{self.tag}._oscjson._tcp.local.", port=self.http_port,
            properties={"txtvers": "1"}, server=f"{self.tag}.oscjson.local.", addresses=["127.0.0.1"]))
        self.zc.register_service(ServiceInfo(
            "_osc._udp.local.", f"{self.tag}._osc._udp.local.", port=self.osc_port,
            properties={"txtvers": "1"}, server=f"{self.tag}.osc.local.", addresses=["127.0.0.1"]))

    def stop(self):
        if self.zc:
            self.zc.unregister_all_services()
            self.zc.close()
            self.zc = None
        self.http.shutdown()
        self.http.server_close()


def vrchat_port():
    try:
        logs = [os.path.join(VRC_LOGS, f) for f in os.listdir(VRC_LOGS) if f.startswith("output_log_")]
        port = None
        with open(max(logs, key=os.path.getmtime), encoding="utf-8", errors="replace") as f:
            for line in f:
                m = VRC_PORT.search(line)
                if m:
                    port = int(m.group(1))
        return port
    except (OSError, ValueError):
        return None


def fetch(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=3) as r:
        return json.load(r)


def collect(node, out):
    if not isinstance(node, dict):
        return
    value = node.get("VALUE")
    if isinstance(value, list) and value and isinstance(node.get("FULL_PATH"), str):
        out[node["FULL_PATH"]] = value[0]
    contents = node.get("CONTENTS")
    if isinstance(contents, dict):
        for child in contents.values():
            collect(child, out)


def read_values(port):
    try:
        info = fetch(port, "/?HOST_INFO")
        if not str(info.get("NAME", "")).startswith("VRChat-Client-"):
            return None
        out = {}
        collect(fetch(port, "/avatar/parameters"), out)
        return out
    except (OSError, ValueError, AttributeError):
        return None


def vrchat_values():
    port = found.get("port")
    values = read_values(port) if port else None
    if values is None:
        fresh = vrchat_port()
        if fresh and fresh != port:
            values = read_values(fresh)
            if values is not None:
                found["port"] = fresh
    return values

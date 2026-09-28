import json
import secrets
import threading
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
        self.name = "VibeVRC-" + secrets.token_hex(3).upper()
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
            "_oscjson._tcp.local.", f"{self.name}._oscjson._tcp.local.", port=self.http_port,
            properties={"txtvers": "1"}, server=f"{self.name}.oscjson.local.", addresses=["127.0.0.1"]))
        self.zc.register_service(ServiceInfo(
            "_osc._udp.local.", f"{self.name}._osc._udp.local.", port=self.osc_port,
            properties={"txtvers": "1"}, server=f"{self.name}.osc.local.", addresses=["127.0.0.1"]))

    def stop(self):
        if self.zc:
            self.zc.unregister_all_services()
            self.zc.close()
            self.zc = None
        self.http.shutdown()

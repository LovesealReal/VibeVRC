import asyncio
import ipaddress
import json
import socket

from websockets.asyncio.client import connect


def local_networks():
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    nets = []
    for ip in sorted(ips):
        a = ipaddress.ip_address(ip)
        if a.is_private and not a.is_loopback and not a.is_link_local:
            n = ipaddress.ip_network(f"{ip}/24", strict=False)
            if n not in nets:
                nets.append(n)
    return nets[:4], ips


async def _open(ip, port, sem):
    async with sem:
        try:
            _, w = await asyncio.wait_for(asyncio.open_connection(ip, port), 0.8)
            w.close()
            return ip
        except Exception:
            return None


async def _server_name(ip, port):
    try:
        async with connect(f"ws://{ip}:{port}", open_timeout=3, ping_interval=None) as ws:
            await ws.send(json.dumps([{"RequestServerInfo": {"Id": 1, "ClientName": "VibeVRC", "MessageVersion": 3}}]))
            for m in json.loads(await asyncio.wait_for(ws.recv(), 3)):
                if "ServerInfo" in m:
                    return m["ServerInfo"].get("ServerName") or "Intiface"
    except Exception:
        return None


async def find_intiface(port=12345):
    nets, mine = local_networks()
    hosts = [str(h) for n in nets for h in n.hosts() if str(h) not in mine]
    sem = asyncio.Semaphore(128)
    hits = [h for h in await asyncio.gather(*(_open(h, port, sem) for h in hosts)) if h]
    found = []
    for ip in hits:
        name = await _server_name(ip, port)
        if name:
            found.append({"address": f"{ip}:{port}", "name": name})
    return found

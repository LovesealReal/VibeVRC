import asyncio
import ctypes
import ipaddress
import json
import socket

from websockets.asyncio.client import connect

AF_INET = 2
GAA_FLAGS = 0x2 | 0x4 | 0x8 | 0x80
ERROR_BUFFER_OVERFLOW = 111
IF_UP = 1
ETHERNET, WIFI = 6, 71
TIMEOUT = 2.5
SEARCH_TIME = 20
MAX_HOSTS = 1024
SKIP = ("hyper-v", "vethernet", "virtualbox", "vmware", "tailscale", "zerotier", "wintun", "wireguard",
        "tap-windows", "openvpn", "hamachi", "radmin", "npcap", "loopback", "bluetooth", "vpn", "docker", "wsl")


class SockAddr(ctypes.Structure):
    _fields_ = [("family", ctypes.c_ushort), ("port", ctypes.c_ushort), ("addr", ctypes.c_ubyte * 4),
                ("zero", ctypes.c_ubyte * 8)]


class SocketAddress(ctypes.Structure):
    _fields_ = [("sockaddr", ctypes.c_void_p), ("length", ctypes.c_int)]


class Unicast(ctypes.Structure):
    pass


Unicast._fields_ = [("Length", ctypes.c_ulong), ("Flags", ctypes.c_ulong), ("Next", ctypes.POINTER(Unicast)),
                    ("Address", SocketAddress), ("PrefixOrigin", ctypes.c_int), ("SuffixOrigin", ctypes.c_int),
                    ("DadState", ctypes.c_int), ("ValidLifetime", ctypes.c_ulong),
                    ("PreferredLifetime", ctypes.c_ulong), ("LeaseLifetime", ctypes.c_ulong),
                    ("OnLinkPrefixLength", ctypes.c_ubyte)]


class Gateway(ctypes.Structure):
    pass


Gateway._fields_ = [("Length", ctypes.c_ulong), ("Reserved", ctypes.c_ulong), ("Next", ctypes.POINTER(Gateway)),
                    ("Address", SocketAddress)]


class Adapter(ctypes.Structure):
    pass


Adapter._fields_ = [("Length", ctypes.c_ulong), ("IfIndex", ctypes.c_ulong), ("Next", ctypes.POINTER(Adapter)),
                    ("AdapterName", ctypes.c_char_p), ("FirstUnicastAddress", ctypes.POINTER(Unicast)),
                    ("FirstAnycastAddress", ctypes.c_void_p), ("FirstMulticastAddress", ctypes.c_void_p),
                    ("FirstDnsServerAddress", ctypes.c_void_p), ("DnsSuffix", ctypes.c_wchar_p),
                    ("Description", ctypes.c_wchar_p), ("FriendlyName", ctypes.c_wchar_p),
                    ("PhysicalAddress", ctypes.c_ubyte * 8), ("PhysicalAddressLength", ctypes.c_ulong),
                    ("Flags", ctypes.c_ulong), ("Mtu", ctypes.c_ulong), ("IfType", ctypes.c_ulong),
                    ("OperStatus", ctypes.c_int), ("Ipv6IfIndex", ctypes.c_ulong),
                    ("ZoneIndices", ctypes.c_ulong * 16), ("FirstPrefix", ctypes.c_void_p),
                    ("TransmitLinkSpeed", ctypes.c_uint64), ("ReceiveLinkSpeed", ctypes.c_uint64),
                    ("FirstWinsServerAddress", ctypes.c_void_p), ("FirstGatewayAddress", ctypes.POINTER(Gateway)),
                    ("Ipv4Metric", ctypes.c_ulong)]


def ipv4(address):
    if not address.sockaddr:
        return None
    sa = SockAddr.from_address(address.sockaddr)
    return socket.inet_ntoa(bytes(sa.addr)) if sa.family == AF_INET else None


def adapters():
    api = ctypes.WinDLL("iphlpapi")
    api.GetAdaptersAddresses.argtypes = [ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p,
                                         ctypes.POINTER(ctypes.c_ulong)]
    api.GetAdaptersAddresses.restype = ctypes.c_ulong
    size = ctypes.c_ulong(16384)
    for _ in range(4):
        buf = ctypes.create_string_buffer(size.value)
        err = api.GetAdaptersAddresses(AF_INET, GAA_FLAGS, None, buf, ctypes.byref(size))
        if err != ERROR_BUFFER_OVERFLOW:
            break
    if err:
        raise OSError(err, "GetAdaptersAddresses failed")
    found = []
    p = ctypes.cast(buf, ctypes.POINTER(Adapter))
    while p:
        a = p.contents
        addrs, u = [], a.FirstUnicastAddress
        while u:
            ip = ipv4(u.contents.Address)
            if ip:
                addrs.append((ip, u.contents.OnLinkPrefixLength))
            u = u.contents.Next
        gateway, g = False, a.FirstGatewayAddress
        while g:
            gateway = gateway or ipv4(g.contents.Address) not in (None, "0.0.0.0")
            g = g.contents.Next
        found.append({"name": a.FriendlyName or "", "desc": a.Description or "", "type": a.IfType,
                      "up": a.OperStatus == IF_UP, "addrs": addrs, "gateway": gateway, "metric": a.Ipv4Metric})
        p = a.Next
    return found


def usable(ip):
    a = ipaddress.ip_address(ip)
    return a.is_private and not a.is_loopback and not a.is_link_local


def subnet(ip, prefix):
    net = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
    return net if net.num_addresses <= MAX_HOSTS else ipaddress.ip_network(f"{ip}/24", strict=False)


def from_adapters():
    picks = []
    for a in adapters():
        skip = any(w in f"{a['name']} {a['desc']}".lower() for w in SKIP)
        if not a["up"] or a["type"] not in (ETHERNET, WIFI) or (skip and not a["gateway"]):
            continue
        for ip, prefix in a["addrs"]:
            if usable(ip):
                picks.append((skip, not a["gateway"], a["metric"], ip, subnet(ip, prefix), a["name"]))
    return [p[3:] for p in sorted(picks, key=lambda p: p[:3])]


def from_hostname():
    ips = set()
    try:
        ips.update(info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    return [(ip, subnet(ip, 24), "network") for ip in sorted(ips) if usable(ip)][:4]


def networks():
    try:
        picks = from_adapters()
    except (OSError, AttributeError, ValueError):
        picks = []
    picks = picks or from_hostname()
    seen, out = set(), []
    for ip, net, name in picks:
        if net not in seen:
            seen.add(net)
            out.append((ip, net, name))
    return out, {ip for ip, _, _ in picks}


async def is_open(ip, port, sem):
    async with sem:
        try:
            _, w = await asyncio.wait_for(asyncio.open_connection(ip, port), TIMEOUT)
            w.close()
            return ip
        except (OSError, asyncio.TimeoutError):
            return None


async def server_name(ip, port):
    try:
        async with connect(f"ws://{ip}:{port}", open_timeout=4, ping_interval=None, proxy=None) as ws:
            await ws.send(json.dumps([{"RequestServerInfo": {"Id": 1, "ClientName": "VibeVRC", "MessageVersion": 3}}]))
            for m in json.loads(await asyncio.wait_for(ws.recv(), 4)):
                if "ServerInfo" in m:
                    return m["ServerInfo"].get("ServerName") or "Intiface"
    except Exception:
        return None


async def probe(ip, port, sem):
    if await is_open(ip, port, sem):
        return ip, await server_name(ip, port)
    return None


async def search(hosts, port, sem):
    tasks = [asyncio.create_task(probe(h, port, sem)) for h in hosts]
    pending = set(tasks)
    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            if any(t.result() and t.result()[1] for t in done):
                if pending:
                    await asyncio.wait(pending, timeout=1)
                break
    finally:
        for t in tasks:
            t.cancel()
    return [t.result() for t in tasks if t.done() and not t.cancelled() and t.result()]


async def find_intiface(log=print, port=12345, search_time=SEARCH_TIME, connected=lambda: None):
    nets, mine = await asyncio.to_thread(networks)
    if not nets:
        log("Couldn't find a network to search. Is this PC on Wi-Fi or Ethernet?")
        return []
    for ip, net, name in nets:
        log(f"Looking for Intiface on {net} ({name}, this PC is {ip}).")
    hosts = list(dict.fromkeys(str(h) for _, net, _ in nets for h in net.hosts() if str(h) not in mine))
    if not hosts:
        log("There's nothing else on this network to search.")
        return []
    sem = asyncio.Semaphore(512)
    loop = asyncio.get_running_loop()
    end = loop.time() + search_time
    odd = set()
    while True:
        start = loop.time()
        address = connected()
        if address:
            log(f"Connected to Intiface at {address}.")
            return [{"address": address, "name": "Intiface"}]
        results = await search(hosts, port, sem)
        found = [{"address": f"{ip}:{port}", "name": name} for ip, name in results if name]
        if found:
            log("Found Intiface at " + ", ".join(f["address"] for f in found) + ".")
            return found
        for ip, _ in results:
            if ip not in odd:
                odd.add(ip)
                log(f"{ip} has port {port} open but didn't answer like Intiface.")
        if loop.time() + TIMEOUT > end:
            break
        await asyncio.sleep(max(0, start + TIMEOUT - loop.time()))
    log(f"Didn't find Intiface on port {port}.")
    return []

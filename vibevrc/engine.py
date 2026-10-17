import asyncio
import ctypes
import os
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse

ENGINE_EXE = "intiface-engine.exe"
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
POLL = 3
BUSY_AFTER = 10
MAX_PING_MS = 4000

TH32CS_SNAPPROCESS = 0x2
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
PROCESS_SET_QUOTA_AND_TERMINATE = 0x0101
SYNCHRONIZE = 0x00100000
WAIT_TIMEOUT = 0x102
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x1

jobs = []


def keep_awake(on):
    if sys.platform == "win32":
        k32 = ctypes.windll.kernel32
        k32.SetThreadExecutionState.argtypes = [ctypes.c_uint32]
        k32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))


def port_open(host, port):
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


class ProcessEntry(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_uint32), ("cntUsage", ctypes.c_uint32), ("th32ProcessID", ctypes.c_uint32),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", ctypes.c_uint32),
                ("cntThreads", ctypes.c_uint32), ("th32ParentProcessID", ctypes.c_uint32),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", ctypes.c_uint32), ("szExeFile", ctypes.c_wchar * 260)]


class BasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32), ("SchedulingClass", ctypes.c_uint32)]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", ctypes.c_uint64 * 6),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


def snapshot():
    if sys.platform != "win32":
        return []
    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == ctypes.c_void_p(-1).value:
        return []
    found = []
    entry = ProcessEntry()
    entry.dwSize = ctypes.sizeof(ProcessEntry)
    try:
        ok = k32.Process32FirstW(ctypes.c_void_p(snap), ctypes.byref(entry))
        while ok:
            found.append((entry.szExeFile.lower(), entry.th32ProcessID))
            ok = k32.Process32NextW(ctypes.c_void_p(snap), ctypes.byref(entry))
    finally:
        k32.CloseHandle(ctypes.c_void_p(snap))
    return found


def running_processes():
    return {name for name, _ in snapshot()}


def open_process(name):
    pid = next((p for n, p in snapshot() if n == name), None)
    if pid is None:
        return None
    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = ctypes.c_void_p
    return k32.OpenProcess(SYNCHRONIZE, False, pid) or None


def exited(handle):
    k32 = ctypes.windll.kernel32
    k32.WaitForSingleObject.restype = ctypes.c_uint32
    return k32.WaitForSingleObject(ctypes.c_void_p(handle), 0) != WAIT_TIMEOUT


def close_process(handle):
    ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))


def central_open(procs):
    return any("intiface" in p and "central" in p for p in procs)


def kill_with_us(proc):
    if sys.platform != "win32":
        return
    k32 = ctypes.windll.kernel32
    k32.CreateJobObjectW.restype = ctypes.c_void_p
    k32.OpenProcess.restype = ctypes.c_void_p
    job = k32.CreateJobObjectW(None, None)
    info = ExtendedLimits()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    k32.SetInformationJobObject(ctypes.c_void_p(job), JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                ctypes.byref(info), ctypes.sizeof(info))
    handle = k32.OpenProcess(PROCESS_SET_QUOTA_AND_TERMINATE, False, proc.pid)
    k32.AssignProcessToJobObject(ctypes.c_void_p(job), ctypes.c_void_p(handle))
    k32.CloseHandle(ctypes.c_void_p(handle))
    jobs.append(job)


def find_engine():
    here = os.path.dirname(os.path.abspath(__file__))
    spots = [getattr(sys, "_MEIPASS", ""), os.path.dirname(sys.executable), os.path.join(os.path.dirname(here), "intiface")]
    for d in spots:
        p = os.path.join(d, ENGINE_EXE)
        if d and os.path.exists(p):
            return p
    return ""


def is_remote(url):
    return (urlparse(url).hostname or "127.0.0.1") not in LOCAL_HOSTS


class EngineSupervisor:
    def __init__(self, url, engine_path, log_path, log, is_connected):
        u = urlparse(url)
        self.host = u.hostname or "127.0.0.1"
        self.port = u.port or 12345
        self.remote = is_remote(url)
        self.exe = engine_path
        self.log_path = log_path
        self.log = log
        self.is_connected = is_connected
        self.proc = None
        self.logf = None
        self.fails = 0
        self.mode = "starting"
        self.stop_toys = None
        self.taken = None

    async def watch_phone(self):
        self.mode = "phone"
        waiting = False
        while True:
            if self.is_connected():
                waiting = False
            elif not waiting:
                waiting = True
                self.log(f"Waiting for the phone at {self.host}:{self.port}. Keep Intiface Central open and the "
                         "phone on the same Wi-Fi as this PC.")
            await asyncio.sleep(3)

    async def run(self):
        if self.remote:
            await self.watch_phone()
            return
        if any("lovense" in p for p in await asyncio.to_thread(running_processes)):
            self.log("Lovense Remote is open. Close it so VibeVRC can reach the toy.")
        said = None
        while True:
            ours = self.proc is not None and self.proc.poll() is None
            if ours and central_open(await asyncio.to_thread(running_processes)):
                self.log("Intiface Central opened. Letting it take the toy.")
                if self.stop_toys:
                    await self.stop_toys()
                self.stop()
                await asyncio.sleep(1)
                continue
            if not ours:
                if self.proc is not None:
                    self.fails += 1
                    self.log("Toy engine stopped. Restarting it.")
                    self.stop()
                    if self.fails >= 3:
                        self.log("Toy engine keeps stopping. Is Bluetooth on? Trying again in 30s.")
                        self.fails = 0
                        await asyncio.sleep(30)
                connected = self.is_connected()
                taken = not connected and await asyncio.to_thread(port_open, self.host, self.port)
                self.taken = (self.taken or time.monotonic()) if taken else None
                if taken and time.monotonic() - self.taken > BUSY_AFTER:
                    said = self.say(said, "busy", f"Another app is using port {self.port}, so the toy engine "
                                    "can't start. Close other toy apps.")
                elif connected:
                    said = self.say(said, "external", "Using Intiface Central.")
                elif taken:
                    self.mode = "external"
                elif central_open(await asyncio.to_thread(running_processes)):
                    said = self.say(said, "central", "Intiface Central is open but not started. "
                                    "Press Start Server in it, or close it.")
                elif self.exe:
                    try:
                        await self.start()
                        said = self.mode = "ours"
                    except OSError as e:
                        self.stop()
                        said = self.say(said, "none", f"Couldn't start the toy engine ({e}). "
                                        "Open Intiface Central and press Start Server.")
                else:
                    said = self.say(said, "none", "Waiting for Intiface Central. Open it and press Start Server.")
            await asyncio.sleep(POLL)

    def say(self, said, key, msg):
        self.mode = key
        if said != key:
            self.log(msg)
        return key

    async def start(self):
        self.logf = open(self.log_path, "a" if self.fails else "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            [self.exe, "--websocket-port", str(self.port), "--server-name", "VibeVRC Engine",
             "--max-ping-time", str(MAX_PING_MS), "--log", "info", "--use-bluetooth-le", "--use-lovense-dongle-hid"],
            stdout=self.logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=dict(os.environ, NO_COLOR="1"),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        kill_with_us(self.proc)
        self.log("Starting toy engine. Turn your toy on and it will connect.")
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and self.proc.poll() is None:
            if self.is_connected() or await asyncio.to_thread(port_open, self.host, self.port):
                break
            await asyncio.sleep(0.2)
        if self.proc.poll() is not None:
            self.log("Toy engine didn't start. Check engine.log in the settings folder.")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(2)
            except subprocess.TimeoutExpired:
                pass
        self.proc = None
        if self.logf:
            self.logf.close()
            self.logf = None

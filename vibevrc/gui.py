import asyncio
import ctypes
import os
import re
import subprocess
import sys
import threading
import time

import webview

from . import __version__, finder, settings
from .core import Core

ERROR_ALREADY_EXISTS = 183


def resource(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def already_running():
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW(None, False, "VibeVRC.SingleInstance")
    return k32.GetLastError() == ERROR_ALREADY_EXISTS


def to_clipboard(text):
    try:
        subprocess.run(["clip"], input=text, text=True, check=True, timeout=5,
                       creationflags=subprocess.CREATE_NO_WINDOW)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def phone_url(raw):
    raw = raw.strip().split("://")[-1].strip("/")
    if raw.count(":") == 1:
        host, port = raw.split(":")
    else:
        host, port = raw, ""
    port = port or "12345"
    if not re.fullmatch(r"[A-Za-z0-9.\-]+", host) or not port.isdigit() or not 0 < int(port) < 65536:
        return None
    return f"ws://{host}:{port}"


def sleep_window(win, sleeping):
    from System import Action
    from Microsoft.Web.WebView2.Core import CoreWebView2MemoryUsageTargetLevel as Level
    form = win.native

    def apply():
        view = form.webview
        if sleeping:
            view.Visible = False
            view.CoreWebView2.MemoryUsageTargetLevel = Level.Low
            view.CoreWebView2.TrySuspendAsync()
        else:
            view.CoreWebView2.MemoryUsageTargetLevel = Level.Normal
            view.Visible = True

    try:
        form.Invoke(Action(apply))
    except Exception:
        return
    if sleeping:
        k32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.EmptyWorkingSet.argtypes = [ctypes.c_void_p]
        psapi.EmptyWorkingSet(k32.GetCurrentProcess())


class Api:
    def __init__(self):
        self._home = settings.home()
        self._cfg = settings.load(self._home)
        self._core = Core(self._cfg, self._home)
        self._loop = asyncio.new_event_loop()
        started = threading.Event()
        threading.Thread(target=self._run, args=(started,), daemon=True).start()
        started.wait(15)

    def _run(self, started):
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._core.start(discover=self._cfg.setup_done))
        finally:
            started.set()
        self._loop.run_forever()

    def _call(self, fn, *args, timeout=15):
        async def wrap():
            result = fn(*args)
            if asyncio.iscoroutine(result):
                result = await result
            return result
        return asyncio.run_coroutine_threadsafe(wrap(), self._loop).result(timeout)

    def _save(self):
        settings.save(self._cfg, self._home)

    def _shutdown(self):
        try:
            self._call(self._core.stop, timeout=8)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)

    def state(self, with_log=False):
        s = self._call(self._core.state, bool(with_log))
        s["setup_done"] = self._cfg.setup_done
        s["version"] = __version__
        return s

    def finish_setup(self, role, name):
        if name.strip():
            self._call(self._core.set_name, name.strip()[:32])
        if role == "share":
            self._call(self._core.set_sharing, True)
        self._cfg.setup_done = True
        self._save()
        self._call(self._core.start_discovery)

    def set_name(self, name):
        self._call(self._core.set_name, name.strip()[:32])
        self._save()

    def add_target(self, name, code):
        name = name.strip()[:32]
        if not name:
            return {"error": "Give them a name."}
        if len(self._cfg.targets) >= 8:
            return {"error": "You can have up to 8 people."}
        try:
            added = self._call(self._core.add_target, name, code)
        except ValueError:
            return {"error": "That isn't a share code. It starts with VV-."}
        if not added:
            return {"error": "You already added that code."}
        self._save()
        return {"ok": True}

    def remove_target(self, idx):
        self._call(self._core.remove_target, int(idx))
        self._save()

    def test_target(self, idx):
        asyncio.run_coroutine_threadsafe(self._core.controller.test(int(idx)), self._loop)

    def set_sharing(self, on):
        self._call(self._core.set_sharing, bool(on))
        self._save()

    def new_code(self):
        self._call(self._core.new_code)

    def copy_code(self):
        code = self._core.code or self._core.peek_code()
        return bool(code) and to_clipboard(code)

    def copy_log(self):
        return to_clipboard("\n".join(self._core.lines))

    def use_pc(self):
        self._call(self._core.set_toy_source, "ws://127.0.0.1:12345")
        self._save()

    def use_phone(self, address):
        url = phone_url(address or "")
        if not url:
            return {"error": "That doesn't look right. Example: 192.168.1.23:12345"}
        self._call(self._core.set_toy_source, url)
        self._save()
        return {"ok": True}

    def find_phone(self):
        try:
            return {"found": self._call(finder.find_intiface, timeout=40)}
        except Exception as e:
            return {"error": f"Search failed: {e}"}

    def set_max(self, value):
        self._call(self._core.set_max, float(value))
        self._save()

    def set_paused(self, on):
        self._call(self._core.set_paused, bool(on))

    def set_sending_paused(self, on):
        self._call(self._core.controller.set_panic, bool(on))

    def open_folder(self):
        os.startfile(self._home)


def webview2_installed():
    import winreg
    key = r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    spots = [(winreg.HKEY_LOCAL_MACHINE, key.replace("SOFTWARE\\", "SOFTWARE\\WOW6432Node\\")),
             (winreg.HKEY_LOCAL_MACHINE, key), (winreg.HKEY_CURRENT_USER, key)]
    for root, path in spots:
        try:
            with winreg.OpenKey(root, path) as k:
                version = winreg.QueryValueEx(k, "pv")[0]
        except OSError:
            continue
        if version and version != "0.0.0.0":
            return True
    return False


def log_crashes(home):
    path = os.path.join(home, "vibevrc-crash.log")

    def write(kind, exc, value, tb):
        import traceback
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {kind}\n")
            f.write("".join(traceback.format_exception(exc, value, tb)) + "\n")

    sys.excepthook = lambda exc, value, tb: write("crash", exc, value, tb)
    threading.excepthook = lambda a: write(f"crash in {a.thread.name if a.thread else 'thread'}",
                                           a.exc_type, a.exc_value, a.exc_traceback)


def main():
    if already_running():
        ctypes.windll.user32.MessageBoxW(None, "VibeVRC is already open.", "VibeVRC", 0x40)
        return
    if not webview2_installed():
        answer = ctypes.windll.user32.MessageBoxW(
            None, "VibeVRC needs Microsoft Edge WebView2 to show its window, and it isn't installed.\n\n"
                  "Click OK to open the download page. Get the Evergreen Bootstrapper, run it, then open VibeVRC again.",
            "VibeVRC", 0x31)
        if answer == 1:
            os.startfile("https://developer.microsoft.com/microsoft-edge/webview2/")
        return
    api = Api()
    log_crashes(api._home)
    win = webview.create_window("VibeVRC", url=resource("ui.html"), js_api=api, width=520, height=780,
                                min_size=(440, 560), background_color="#18171a")
    win.events.minimized += lambda: sleep_window(win, True)
    win.events.restored += lambda: sleep_window(win, False)
    webview.start(gui="edgechromium")
    api._shutdown()

import asyncio
import ctypes
import os
import re
import subprocess
import sys
import threading
import time

import webview

from . import __version__, finder, settings, updater
from .core import Core
from .engine import is_remote

ERROR_ALREADY_EXISTS = 183
CLIP = os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", "System32", "clip.exe")


def resource(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def already_running(wait=0.0):
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    end = time.monotonic() + wait
    while True:
        handle = k32.CreateMutexW(None, False, "VibeVRC.SingleInstance")
        if ctypes.get_last_error() != ERROR_ALREADY_EXISTS:
            return False
        k32.CloseHandle(handle)
        if time.monotonic() > end:
            return True
        time.sleep(0.25)


def to_clipboard(text):
    try:
        subprocess.run([CLIP], input=text.encode("utf-16-le"), check=True, timeout=5,
                       creationflags=subprocess.CREATE_NO_WINDOW)
        return True
    except (OSError, ValueError, subprocess.SubprocessError):
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


def purge_callbacks():
    for w in webview.windows:
        calls = getattr(w, "_callbacks", None)
        if isinstance(calls, dict) and len(calls) > 200:
            for k in [k for k, v in list(calls.items()) if v is None]:
                calls.pop(k, None)


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
    def __init__(self, home):
        self._home = home
        self._cfg = settings.load(self._home)
        self._core = Core(self._cfg, self._home)
        self._loop = asyncio.new_event_loop()
        self._update = None
        self._failed = None
        self.restart = None
        started = threading.Event()
        threading.Thread(target=self._run, args=(started,), daemon=True).start()
        started.wait(15)
        if self._failed:
            raise self._failed
        if "--updated" in sys.argv:
            self._log(f"Updated to {__version__}.")
        if self._cfg.check_updates:
            threading.Thread(target=self._check_update, daemon=True).start()

    def _run(self, started):
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._core.start(discover=self._cfg.setup_done))
        except Exception as e:
            self._failed = e
            self._core.log(f"Couldn't start: {e!r}")
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

    def _log(self, msg):
        self._loop.call_soon_threadsafe(self._core.log, msg)

    def _check_update(self):
        try:
            self._update = updater.check()
        except Exception:
            return
        if self._update:
            self._log(f"VibeVRC {self._update['version']} is out. Click Update at the top to get it.")

    def _save(self):
        self._call(self._core.save)

    def _shutdown(self):
        try:
            self._call(self._core.stop, timeout=8)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)

    def state(self, with_log=False):
        purge_callbacks()
        s = self._call(self._core.state, bool(with_log))
        s["setup_done"] = self._cfg.setup_done
        s["version"] = __version__
        s["update"] = self._update and self._update["version"]
        return s

    def update(self):
        info = self._update
        if not info:
            return {"error": "No update found."}
        if not updater.can_install():
            os.startfile(updater.PAGE)
            return {"opened": True}
        self._log(f"Downloading VibeVRC {info['version']}.")
        try:
            self.restart = updater.install(info)
        except Exception as e:
            self._log(f"Update failed: {e}")
            return {"error": "Couldn't update. You can download it from GitHub instead."}
        webview.windows[0].destroy()
        return {"ok": True}

    def open_releases(self):
        os.startfile(updater.PAGE)

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

    def _phone_connected(self):
        intiface = self._core.intiface
        if intiface and intiface.ws is not None and is_remote(self._cfg.intiface_url):
            return self._cfg.intiface_url.split("://")[-1]
        return None

    def find_phone(self):
        core = self._core
        address = self._phone_connected()
        if address:
            return {"found": [{"address": address, "name": "Intiface"}]}
        try:
            return {"found": self._call(finder.find_intiface, core.log, 12345, finder.SEARCH_TIME,
                                        self._phone_connected, timeout=40)}
        except Exception as e:
            core.log(f"Phone search failed: {e!r}")
            return {"error": "Search failed. Try typing the address instead."}

    def set_max(self, value):
        self._call(self._core.set_max, float(value))
        self._save()

    def set_paused(self, on):
        self._call(self._core.set_paused, bool(on))

    def set_sending_paused(self, on):
        self._call(self._core.set_stop_sending, bool(on))

    def set_haptics(self, changes):
        names = {"on": "haptics", "send": "haptics_send", "self": "haptics_self", "touch": "haptics_touch",
                 "motion": "haptics_motion", "strength": "haptics_strength", "depth": "haptics_depth",
                 "flip": "haptics_flip"}
        kw = {names[k]: max(0.0, min(1.0, float(v))) if k == "strength" else bool(v)
              for k, v in changes.items() if k in names}
        self._call(lambda: self._core.set_haptics(**kw))

    def set_zone(self, key, on):
        off = set(self._cfg.haptics_off)
        if on:
            off.discard(key)
        else:
            off.add(str(key))
        self._cfg.haptics_off = sorted(off)
        self._call(self._core.apply_haptics)
        self._save()

    def open_folder(self):
        os.startfile(self._home)

    def open_licenses(self):
        for path in (resource("THIRD-PARTY-NOTICES.txt"),
                     os.path.join(settings.app_dir(), "build", "THIRD-PARTY-NOTICES.txt")):
            if os.path.exists(path):
                os.startfile(path)
                return


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
        try:
            with open(path, "a", encoding="utf-8", errors="replace") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {kind}\n")
                f.write("".join(traceback.format_exception(exc, value, tb)) + "\n")
        except OSError:
            pass

    sys.excepthook = lambda exc, value, tb: write("crash", exc, value, tb)
    threading.excepthook = lambda a: write(f"crash in {a.thread.name if a.thread else 'thread'}",
                                           a.exc_type, a.exc_value, a.exc_traceback)


def main():
    updated = "--updated" in sys.argv
    if already_running(20 if updated else 0):
        ctypes.windll.user32.MessageBoxW(None, "VibeVRC is already open. Check your taskbar.", "VibeVRC", 0x40)
        return
    if not webview2_installed():
        answer = ctypes.windll.user32.MessageBoxW(
            None, "VibeVRC needs Microsoft Edge WebView2 to show its window, and it isn't installed.\n\n"
                  "Click OK to open the download page. Get the Evergreen Bootstrapper, run it, then open VibeVRC again.",
            "VibeVRC", 0x31)
        if answer == 1:
            os.startfile("https://developer.microsoft.com/microsoft-edge/webview2/")
        return
    home = settings.home()
    log_crashes(home)
    try:
        api = Api(home)
    except Exception as e:
        sys.excepthook(type(e), e, e.__traceback__)
        ctypes.windll.user32.MessageBoxW(
            None, f"VibeVRC couldn't start. The details are in vibevrc-crash.log in this folder:\n\n{home}",
            "VibeVRC", 0x10)
        return
    win = webview.create_window("VibeVRC", url=resource("ui.html"), js_api=api, width=520, height=780,
                                min_size=(440, 560), background_color="#18171a")
    win.events.minimized += lambda: sleep_window(win, True)
    win.events.restored += lambda: sleep_window(win, False)
    win.events.loaded += lambda: threading.Thread(target=updater.cleanup, daemon=True).start()
    try:
        webview.start(gui="edgechromium")
    finally:
        api._shutdown()
    if api.restart:
        updater.relaunch(api.restart)

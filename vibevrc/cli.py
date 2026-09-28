import argparse
import asyncio
import os
import sys

from . import settings
from .core import Core


async def keyboard(on_space):
    try:
        import msvcrt
    except ImportError:
        await asyncio.Event().wait()
        return
    while True:
        while msvcrt.kbhit():
            ch = msvcrt.getwch().lower()
            if ch == " ":
                on_space()
            elif ch == "q":
                return
        await asyncio.sleep(0.1)


async def run(args, cfg, home):
    core = Core(cfg, home, echo=lambda line: print(line, flush=True), verbose=args.verbose)
    await core.start(listen=args.test is None, new_code=args.new_code)
    if cfg.share:
        print(f"\n  Share code: {core.code}\n")
    targets = core.controller.targets
    if targets:
        core.log("Targets: " + ", ".join(f"{i} {t.nick}" for i, t in enumerate(targets, 1)))

    def toggle_pause():
        paused = not core.controller.panic
        core.set_paused(paused)
        core.controller.set_panic(paused)

    try:
        if args.test is None:
            core.log("Space: pause. Q: quit.")
            await keyboard(toggle_pause)
            return
        if not 0 <= args.test < len(targets):
            core.log("No target with that number.")
            return
        target = targets[args.test]
        for _ in range(150):
            if target.online and target.toys:
                break
            await asyncio.sleep(0.1)
        else:
            core.log(f"{target.nick} isn't ready. Sending anyway.")
        await core.controller.test(args.test)
        await asyncio.sleep(1)
    finally:
        await core.stop()


def main():
    ap = argparse.ArgumentParser(prog="vibevrc")
    ap.add_argument("--home", required=True, help="folder with settings.json")
    ap.add_argument("--new-code", action="store_true", help="make a new share code")
    ap.add_argument("--test", type=int, metavar="N", help="buzz target N at 50%% for 3s, then quit")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    if args.test is not None:
        args.test -= 1
    home = os.path.abspath(args.home)
    cfg = settings.load(home)
    try:
        asyncio.run(run(args, cfg, home))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()

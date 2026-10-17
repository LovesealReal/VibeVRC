import importlib.metadata as md
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "build", "THIRD-PARTY-NOTICES.txt")
PACKAGES = ["python-osc", "websockets", "paho-mqtt", "cryptography", "cffi", "pycparser", "pywebview", "pythonnet",
            "clr_loader", "bottle", "proxy_tools", "typing_extensions", "zeroconf", "ifaddr"]
LINE = "=" * 78

MIT = """Copyright (c) {who}

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit
persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the
Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""

WEBVIEW2 = """Copyright (C) Microsoft Corporation. All rights reserved.

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the
following conditions are met:

   * Redistributions of source code must retain the above copyright notice, this list of conditions and the following
     disclaimer.
   * Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the
     following disclaimer in the documentation and/or other materials provided with the distribution.
   * The name of Microsoft Corporation, or the names of its contributors may not be used to endorse or promote
     products derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES,
INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

https://www.nuget.org/packages/Microsoft.Web.WebView2/License
"""

EDL = """Eclipse Distribution License - v 1.0

Copyright (c) 2007, Eclipse Foundation, Inc. and its licensors.

All rights reserved.

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the
following conditions are met:

   * Redistributions of source code must retain the above copyright notice, this list of conditions and the following
     disclaimer.
   * Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the
     following disclaimer in the documentation and/or other materials provided with the distribution.
   * Neither the name of the Eclipse Foundation, Inc. nor the names of its contributors may be used to endorse or
     promote products derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES,
INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""

ZEROCONF = """zeroconf is under the LGPL. Its source is at https://github.com/python-zeroconf/python-zeroconf and VibeVRC's
source and build.bat are at https://github.com/LovesealReal/VibeVRC, so you can rebuild VibeVRC with your own copy
of zeroconf.
"""

EXTRA = {"paho-mqtt": EDL, "zeroconf": ZEROCONF}


def engine_version():
    try:
        out = subprocess.run([os.path.join(ROOT, "intiface", "intiface-engine.exe"), "--version"], capture_output=True,
                             text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    found = re.search(r"Version (\S+?),", out)
    return " " + found.group(1) if found else ""


def read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read().strip() + "\n"


def license_texts(dist):
    found = []
    for f in dist.files or []:
        name = os.path.basename(str(f)).upper()
        if name.startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS")):
            path = str(f.locate())
            if os.path.isfile(path) and path not in found:
                found.append(path)
    return [read(p) for p in found]


def section(title, *texts):
    return f"{LINE}\n{title}\n{LINE}\n\n" + "\n".join(texts) + "\n"


def main():
    parts = ["VibeVRC uses the software below. Their licenses follow.\n"]
    parts.append(section(f"Intiface Engine{engine_version()}", read(os.path.join(ROOT, "intiface", "LICENSE.txt"))))
    parts.append(section(f"Python {sys.version.split()[0]}", read(os.path.join(sys.base_prefix, "LICENSE.txt"))))
    for name in PACKAGES:
        dist = md.distribution(name)
        meta = dist.metadata
        texts = license_texts(dist)
        kind = (meta.get("License-Expression") or meta.get("License") or "").strip()
        if not texts and kind == "MIT":
            texts = [MIT.format(who=meta.get("Author") or name)]
        if not texts:
            texts = [kind + "\n"]
        if name in EXTRA:
            texts.append(EXTRA[name])
        parts.append(section(f"{dist.name} {dist.version}", *texts))
    parts.append(section("Microsoft .NET libraries (bundled with pythonnet)", MIT.format(who=".NET Foundation and Contributors")))
    parts.append(section("Microsoft Edge WebView2 SDK (bundled with pywebview)", WEBVIEW2))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(parts))


if __name__ == "__main__":
    main()

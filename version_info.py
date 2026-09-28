import os

from vibevrc import __version__

ROOT = os.path.dirname(os.path.abspath(__file__))
nums = [int(n) for n in __version__.split(".")]
nums = tuple((nums + [0, 0, 0, 0])[:4])
dotted = ".".join(str(n) for n in nums[:3])

text = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={nums},
    prodvers={nums},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'Loveseal'),
        StringStruct('FileDescription', 'VibeVRC'),
        StringStruct('FileVersion', '{dotted}'),
        StringStruct('InternalName', 'VibeVRC'),
        StringStruct('LegalCopyright', 'Copyright (c) 2026 Loveseal. MIT License.'),
        StringStruct('OriginalFilename', 'VibeVRC.exe'),
        StringStruct('ProductName', 'VibeVRC'),
        StringStruct('ProductVersion', '{dotted}')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [0x0409, 1200])])
  ]
)
"""

os.makedirs(os.path.join(ROOT, "build"), exist_ok=True)
with open(os.path.join(ROOT, "build", "version_info.txt"), "w", encoding="utf-8") as f:
    f.write(text)

"""Single-file Windows GUI. Build from an isolated requirements-build environment."""

import sys
from importlib.metadata import distribution
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
)

root = Path(SPECPATH)
generated = root / ".build" / "notices"
generated.mkdir(parents=True, exist_ok=True)
python_root = Path(sys.base_prefix)

notices = [
    ("Python " + sys.version.split()[0], python_root / "LICENSE.txt"),
    ("Tcl", root / "packaging-notices" / "Tcl.txt"),
    ("Tk", next((python_root / "tcl").glob("tk*/license.terms"))),
    ("OpenSSL", root / "packaging-notices" / "OpenSSL.txt"),
    ("Steam Frame Passthrough Toggle", root / "packaging-notices" / "Steam-Frame-Passthrough-Toggle.txt"),
]
# Pull notices from the actual installed wheels rather than keeping stale copies.
for name in ("Paramiko", "PyNaCl", "bcrypt", "cffi", "cryptography", "invoke",
             "packaging", "pycparser", "PyInstaller"):
    package = distribution(name)
    files = [entry for entry in package.files if ".dist-info" in entry.parts[0]
             and entry.name.upper().startswith(("LICENSE", "NOTICE", "COPYING"))]
    if not files:
        raise ValueError(f"Missing license for {name}")
    for entry in sorted(files):
        notices.append((f"{name} {package.version} — {entry.name}", package.locate_file(entry)))

notice_path = generated / "THIRD-PARTY-NOTICES.txt"
with notice_path.open("w", encoding="utf-8-sig") as output:
    output.write("VRChat Gesture Dock\nThird-party software notices\n\n")
    for title, path in notices:
        output.write(f"\n{'=' * 72}\n{title}\n{'=' * 72}\n\n")
        output.write(Path(path).read_text(encoding="utf-8"))
        output.write("\n")

data = [(str(root / name), ".") for name in ("dashboard.js", "frame_dock.py", "install-frame.sh")]
data.append((str(notice_path), "."))
# Retain the unmodified LGPL library's source alongside the frozen application.
ssh_library = distribution("paramiko")
for entry in ssh_library.files:
    if entry.parts[0] == "paramiko" and entry.suffix == ".py":
        data.append((str(ssh_library.locate_file(entry)), str(Path("source") / entry.parent)))

analysis = Analysis(
    [str(root / "release_app.py")], pathex=[str(root)], datas=data,
    excludes=["pytest"], noarchive=False,
)
archive = PYZ(analysis.pure)
version = VSVersionInfo(
    ffi=FixedFileInfo(filevers=(1, 0, 0, 0), prodvers=(1, 0, 0, 0),
                      mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[StringFileInfo([StringTable("040904B0", [
        StringStruct("FileDescription", "VRChat Gesture Dock for Steam Frame"),
        StringStruct("FileVersion", "1.0.0"),
        StringStruct("OriginalFilename", "VRChatGestureDock.exe"),
        StringStruct("ProductName", "VRChat Gesture Dock"),
        StringStruct("ProductVersion", "1.0.0"),
    ])]), VarFileInfo([VarStruct("Translation", [1033, 1200])])],
)
exe = EXE(archive, analysis.scripts, analysis.binaries, analysis.datas, [],
          name="VRChatGestureDock", console=False, version=version, upx=False)

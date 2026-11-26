# Copyright (c) 2026 Abdulrahman Aloqayli
# SPDX-License-Identifier: MIT

"""Builds the setup tool as one file (dist/zk-agent-setup, .exe on Windows) with PyInstaller, as CI
does. On Windows the .exe carries its version and copyright (Properties > Details).

  pip install -r requirements.txt pyinstaller
  python build_app.py
"""

import sys
from pathlib import Path

import PyInstaller.__main__

import zk_flasher

HERE = Path(__file__).resolve().parent

WINDOWS_VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(filevers=({v}, 0, 0, 0), prodvers=({v}, 0, 0, 0), mask=0x3F, flags=0x0, OS=0x40004,
    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable("040904B0", [
      StringStruct("CompanyName", "Abdulrahman Aloqayli"),
      StringStruct("FileDescription", "ZK pull agent setup"),
      StringStruct("FileVersion", "{v}"),
      StringStruct("InternalName", "zk-agent-setup"),
      StringStruct("LegalCopyright", "{copyright}. MIT License."),
      StringStruct("OriginalFilename", "zk-agent-setup.exe"),
      StringStruct("ProductName", "ZK pull agent setup"),
      StringStruct("ProductVersion", "{v}"),
    ])]),
    VarFileInfo([VarStruct("Translation", [1033, 1200])]),
  ],
)
"""


def main():
	args = [
		str(HERE / "zk_flasher.py"),
		"--onefile",
		"--windowed",
		"--name", "zk-agent-setup",
		# esptool loads its flasher stubs from data files, which PyInstaller does not find by itself.
		"--collect-data", "esptool",
		"--collect-submodules", "esptool",
		"--distpath", str(HERE / "dist"),
		"--workpath", str(HERE / "build"),
		"--specpath", str(HERE / "build"),
		"--noconfirm",
	]
	if sys.platform == "win32":
		version_file = HERE / "build" / "version_info.txt"
		version_file.parent.mkdir(exist_ok=True)
		version_file.write_text(WINDOWS_VERSION_INFO.format(v=zk_flasher.VERSION, copyright=zk_flasher.COPYRIGHT))
		args += ["--version-file", str(version_file)]
	PyInstaller.__main__.run(args)


if __name__ == "__main__":
	main()

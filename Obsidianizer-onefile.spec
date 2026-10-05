# -*- mode: python ; coding: utf-8 -*-
# Onefile build: .venv\Scripts\pyinstaller Obsidianizer-onefile.spec --distpath dist --workpath build

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []

for pkg in ("pywebview",):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["Obsidianizer.py"],
    pathex=[],
    binaries=binaries,
    datas=datas + [("src/obsidianizer/web", "obsidianizer/web")],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=["runtime_hook_clr.py"],
    excludes=["playwright", "PyQt5", "tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Obsidianizer-Installer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon="Obsidianizer.ico",
)

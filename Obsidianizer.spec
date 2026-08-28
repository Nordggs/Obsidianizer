# -*- mode: python ; coding: utf-8 -*-
# Release build: .venv\Scripts\pyinstaller Obsidianizer.spec --distpath dist --workpath build
# console=False -> no terminal window on launch (GUI app).
# Web assets (app.*, help.html, chat.*, splash.png, icon.png) ship inside
# _internal/obsidianizer/web so ui.RESOURCES resolves correctly onedir.

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
    runtime_hooks=[],
    excludes=["playwright", "PyQt5", "tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Obsidianizer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon="Obsidianizer.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Obsidianizer",
)

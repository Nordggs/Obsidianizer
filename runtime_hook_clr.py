"""Unblock .NET DLLs that Windows Mark-of-the-Web (MOTW) has blocked.

pywebview on Windows uses pythonnet (CoreCLR) to host WebView2.  When the
app is run from a downloaded zip, every DLL carries a ``Zone.Identifier``
alternate data stream that prevents the .NET loader from loading it.  The
``Obsidianizer.exe.config`` ``loadFromRemoteSources`` only helps .NET
Framework, not CoreCLR — so we must strip the ADS ourselves at startup.
"""
import ctypes
import glob
import os
import sys


def _unblock():
    if sys.platform != "win32":
        return
    base = os.path.dirname(sys.executable)
    runtime_dir = os.path.join(base, "_internal", "pythonnet", "runtime")
    if not os.path.isdir(runtime_dir):
        return
    for dll in glob.glob(os.path.join(runtime_dir, "*.dll")):
        try:
            ctypes.windll.kernel32.DeleteFileW(dll + ":Zone.Identifier")
        except OSError:
            pass  # no Zone.Identifier stream — already clean


_unblock()

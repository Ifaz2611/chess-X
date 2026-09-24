#!/usr/bin/env python3
import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

def run(cmd, **kw):
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, check=True, **kw)

def check_platform():
    print("=== Platform check ===")
    sys.path.insert(0, str(SRC))
    import platform_info, input_backend, browser_factory
    print(f"OS: {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"is_wayland: {platform_info.is_wayland()}")
    print(f"is_macos: {platform_info.is_macos()}")
    print(f"scaling: {platform_info.get_scaling_factor()} DPR={platform_info.get_device_pixel_ratio()}")
    print(f"input backends available: {input_backend.list_available_backends()}")
    print(f"browsers: {browser_factory.get_available_browsers()}")
    # also test macOS permissions hint
    if platform_info.is_macos():
        ok, msg = platform_info.check_macos_permissions()
        print(f"macOS permissions ok={ok}: {msg}")
    print("check ok")

def build_pyinstaller(onefile=True):
    # Ensure pyinstaller installed
    try:
        import PyInstaller  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        print("PyInstaller not found, installing...")
        run([sys.executable, "-m", "pip", "install", "pyinstaller"])
    cmd = [sys.executable, "-m", "PyInstaller"]
    if onefile:
        cmd += ["--onefile"]
    else:
        cmd += ["--onedir"]
    cmd += ["--name", "chess-x", "--windowed", "chess-x.spec"]
    if not (ROOT / "chess-x.spec").exists():
        cmd = [sys.executable, "-m", "PyInstaller", "--onefile" if onefile else "--onedir",
               "--windowed", "--name", "chess-x",
               "--add-data", f"src/assets{os.pathsep}assets",
               "--hidden-import", "PyQt6.sip",
               "src/gui.py"]
    run(cmd, cwd=str(ROOT))
    print("PyInstaller build done. Output in dist/")

def build_briefcase():
    import importlib.util
    if importlib.util.find_spec("briefcase") is None:
        print("briefcase not found, installing...")
        run([sys.executable, "-m", "pip", "install", "briefcase"])
    run([sys.executable, "-m", "briefcase", "create"], cwd=str(ROOT))
    run([sys.executable, "-m", "briefcase", "build"], cwd=str(ROOT))
    print("Briefcase build done. Output in build/")

def main():
    ap = argparse.ArgumentParser(description="Chess-X build helper")
    ap.add_argument("--pyinstaller", action="store_true", help="build with PyInstaller")
    ap.add_argument("--briefcase", action="store_true", help="build with briefcase")
    ap.add_argument("--onedir", action="store_true", help="onedir instead of onefile (pyinstaller)")
    ap.add_argument("--check", action="store_true", help="run platform/backend check")
    args = ap.parse_args()
    if args.check or not (args.pyinstaller or args.briefcase):
        check_platform()
    if args.pyinstaller:
        build_pyinstaller(onefile=not args.onedir)
    if args.briefcase:
        build_briefcase()
    if not (args.pyinstaller or args.briefcase or args.check):
        print("No build flag given; --check done.")
if __name__ == "__main__":
    main()

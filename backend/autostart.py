"""Launch on startup: start PCmeter (app mode) when the current user logs in.

Per user only, nothing system-wide, so no admin rights are needed:
  Linux    an XDG autostart entry, ~/.config/autostart/pcmeter.desktop, read at login by KDE Plasma,
           GNOME and the other desktops
  Windows  a value named PCmeter under HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
  macOS    a LaunchAgent, ~/Library/LaunchAgents/com.pcmeter.app.plist, with RunAtLoad

The entry is the source of truth: `enabled()` reads it every time, so turning it off in the system's
own startup settings shows up in PCmeter's menu too.
"""
import os
import platform
import plistlib
import subprocess
import sys
from pathlib import Path

NAME = "PCmeter"
LABEL = "com.pcmeter.app"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def supported(system=None):
    return (system or platform.system()) in ("Linux", "Windows", "Darwin")


def launch_args(system=None):
    """The command that starts this PCmeter in app mode, as an argument list."""
    system = system or platform.system()
    if getattr(sys, "frozen", False):
        link = Path.home() / ".local/bin/pcmeter"   # the installer's link survives updates
        exe = str(link) if system == "Linux" and link.exists() else sys.executable
        return [exe, "--app"]
    python = Path(sys.executable)
    if system == "Windows" and (python.parent / "pythonw.exe").exists():
        python = python.parent / "pythonw.exe"      # no console window at login
    return [str(python), str(Path(__file__).resolve().parent / "pcmeter.py"), "--app"]


# ---- Linux
def xdg_file():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart" / "pcmeter.desktop"


def _exec_quote(arg):
    """Quote one argument for a desktop entry's Exec key (freedesktop rules)."""
    if arg and not any(c in arg for c in ' \t\n"\'\\><~|&;$*?#()`'):
        return arg
    return '"' + "".join("\\" + c if c in '"`$\\' else c for c in arg) + '"'


def _desktop_entry(args):
    return ("[Desktop Entry]\nType=Application\nName=PCmeter\nComment=CPU and GPU desktop gadgets\n"
            f"Exec={' '.join(_exec_quote(a) for a in args)}\nIcon=pcmeter\nTerminal=false\n"
            "StartupNotify=false\nX-GNOME-Autostart-enabled=true\n")


# ---- macOS
def launch_agent_file():
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _launch_agent(args):
    return plistlib.dumps({"Label": LABEL, "ProgramArguments": args, "RunAtLoad": True,
                           "ProcessType": "Interactive"})


# ---- Windows
def _run_key(write=False):
    import winreg
    access = winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE if write else winreg.KEY_QUERY_VALUE
    return winreg, winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, access)


def enabled(system=None):
    system = system or platform.system()
    if system == "Linux":
        return xdg_file().is_file()
    if system == "Darwin":
        return launch_agent_file().is_file()
    if system == "Windows":
        try:
            winreg, key = _run_key()
            with key:
                winreg.QueryValueEx(key, NAME)
            return True
        except OSError:
            return False
    return False


def set_enabled(on, system=None):
    """Add or remove the startup entry. Raises OSError if the system refuses."""
    system = system or platform.system()
    args = launch_args(system)
    if system == "Linux":
        f = xdg_file()
        if on:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(_desktop_entry(args))
        else:
            f.unlink(missing_ok=True)
    elif system == "Darwin":
        f = launch_agent_file()
        if on:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(_launch_agent(args))   # launchd reads it at the next login
        else:
            f.unlink(missing_ok=True)
    elif system == "Windows":
        winreg, key = _run_key(write=True)
        with key:
            if on:
                winreg.SetValueEx(key, NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(args))
            else:
                try:
                    winreg.DeleteValue(key, NAME)
                except FileNotFoundError:
                    pass
    else:
        raise OSError(f"launch on startup is not supported on {system}")

"""Read-only: does the LIVE daemon own a console, and does that console have a window?

Attaches to the target console only to enumerate it, then detaches. Sends nothing,
closes nothing, kills nothing.
"""
import ctypes, json, subprocess, sys
from ctypes import wintypes

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
u32 = ctypes.WinDLL("user32", use_last_error=True)

def daemon_candidates():
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
         "Select-Object ProcessId,ParentProcessId,CommandLine,CreationDate | ConvertTo-Json -Depth 3"],
        capture_output=True, text=True)
    try:
        data = json.loads(out.stdout or "[]")
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    return [d for d in data if "--daemon" in (d.get("CommandLine") or "")]

def probe(pid):
    k32.FreeConsole()
    ok = k32.AttachConsole(wintypes.DWORD(pid))
    if not ok:
        err = ctypes.get_last_error()
        return {"pid": pid, "attached": False, "win32_error": err}
    buf = (wintypes.DWORD * 64)()
    n = k32.GetConsoleProcessList(buf, 64)
    hwnd = k32.GetConsoleWindow()
    info = {"pid": pid, "attached": True, "console_members": list(buf[:n]),
            "console_hwnd": int(hwnd) if hwnd else None}
    if hwnd:
        info["hwnd_is_window"] = bool(u32.IsWindow(hwnd))
        info["hwnd_is_visible"] = bool(u32.IsWindowVisible(hwnd))
        cls = ctypes.create_unicode_buffer(256)
        u32.GetClassNameW(hwnd, cls, 256)
        title = ctypes.create_unicode_buffer(512)
        u32.GetWindowTextW(hwnd, title, 512)
        owner = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        info["hwnd_class"] = cls.value
        info["hwnd_title"] = title.value
        info["hwnd_owner_pid"] = owner.value
    k32.FreeConsole()
    return info

cands = daemon_candidates()
print("daemon processes found: %d" % len(cands))
for c in cands:
    print("  pid=%s ppid=%s created=%s" % (c.get("ProcessId"), c.get("ParentProcessId"), c.get("CreationDate")))
    print("  cmdline=%s" % (c.get("CommandLine") or "")[:160])
print()
for c in cands:
    print(json.dumps(probe(int(c["ProcessId"])), indent=2))
if not cands:
    print("No live --daemon process: the defect is latent, not observable right now.")

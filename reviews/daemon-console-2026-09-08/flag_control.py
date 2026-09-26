"""Control: what actually differs between the old and new daemon launch flags.

Launches a short-lived child through the SAME venv redirector the daemon uses,
once per flag combination, and records for each: the descendant tree, which
process owns the conhost, console membership, and whether a console window
handle exists. Nothing is closed or killed; each child exits on its own.
"""
import ctypes, json, subprocess, time
from ctypes import wintypes

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
u32 = ctypes.WinDLL("user32", use_last_error=True)

VENV_PY = r"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe"
SCRIPT = "import time; time.sleep(8)"

DETACHED_PROCESS         = 0x00000008
CREATE_NO_WINDOW         = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


def snapshot():
    ps = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name | ConvertTo-Json -Depth 2"],
        capture_output=True, text=True)
    return json.loads(ps.stdout or "[]")


def descendants(rows, root):
    out, frontier = [], {root}
    for _ in range(3):
        kids = [r for r in rows if r["ParentProcessId"] in frontier and r["ProcessId"] not in frontier]
        if not kids:
            break
        out.extend(kids)
        frontier |= {r["ProcessId"] for r in kids}
    return out


def console_of(pid):
    k32.FreeConsole()
    if not k32.AttachConsole(wintypes.DWORD(pid)):
        return {"attached": False, "win32_error": ctypes.get_last_error()}
    buf = (wintypes.DWORD * 64)()
    n = k32.GetConsoleProcessList(buf, 64)
    hwnd = k32.GetConsoleWindow()
    me = k32.GetCurrentProcessId()
    info = {"attached": True,
            "members": [p for p in buf[:n] if p != me],
            "console_hwnd": int(hwnd) if hwnd else None}
    k32.FreeConsole()
    return info


for label, flags in (("OLD  DETACHED_PROCESS", DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP),
                     ("NEW  CREATE_NO_WINDOW", CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP)):
    p = subprocess.Popen([VENV_PY, "-c", SCRIPT],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True, creationflags=flags)
    time.sleep(2.0)
    rows = snapshot()
    kids = descendants(rows, p.pid)
    interp = next((k for k in kids if k["Name"].lower() == "python.exe"), None)
    conhost = next((k for k in kids if k["Name"].lower() == "conhost.exe"), None)

    print("%s   flags=0x%08x" % (label, flags))
    print("  wrapper      pid=%-6d console=%s" % (p.pid, console_of(p.pid)))
    if interp:
        print("  interpreter  pid=%-6d console=%s" % (interp["ProcessId"], console_of(interp["ProcessId"])))
    if conhost:
        owner = "wrapper" if conhost["ParentProcessId"] == p.pid else (
                "interpreter" if interp and conhost["ParentProcessId"] == interp["ProcessId"] else
                "pid %d" % conhost["ParentProcessId"])
        print("  conhost      pid=%-6d parent=%s" % (conhost["ProcessId"], owner))
    else:
        print("  conhost      none")
    p.wait(timeout=25)
    print("  child exited rc=%s\n" % p.returncode)

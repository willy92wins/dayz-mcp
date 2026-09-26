"""Read-only: enumerate top-level windows owned by the daemon's private conhost."""
import ctypes, sys
from ctypes import wintypes

u32 = ctypes.WinDLL("user32", use_last_error=True)
TARGETS = {17968: "conhost of daemon 20568", 20568: "daemon interpreter"}

EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
found = []

def cb(hwnd, _lp):
    pid = wintypes.DWORD()
    u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value in TARGETS:
        cls = ctypes.create_unicode_buffer(256)
        u32.GetClassNameW(hwnd, cls, 256)
        title = ctypes.create_unicode_buffer(512)
        u32.GetWindowTextW(hwnd, title, 512)
        rect = wintypes.RECT()
        u32.GetWindowRect(hwnd, ctypes.byref(rect))
        found.append({
            "hwnd": hwnd, "owner_pid": pid.value, "owner": TARGETS[pid.value],
            "class": cls.value, "title": title.value,
            "visible": bool(u32.IsWindowVisible(hwnd)),
            "iconic": bool(u32.IsIconic(hwnd)),
            "rect": (rect.left, rect.top, rect.right, rect.bottom),
        })
    return True

u32.EnumWindows(EnumProc(cb), 0)
if not found:
    print("No top-level window owned by conhost 17968 or daemon 20568.")
    print("=> the private console exists but presents NO desktop window right now.")
for w in found:
    print(w)

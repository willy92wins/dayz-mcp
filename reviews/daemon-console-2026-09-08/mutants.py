import hashlib, pathlib, subprocess, sys

TOOLS = pathlib.Path(r"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools")
PY = TOOLS / ".venv-mcp" / "Scripts" / "python.exe"
DAEMON = TOOLS / "dayz_mcp" / "daemon.py"

ORIG = DAEMON.read_bytes()
ORIG_SHA = hashlib.sha256(ORIG).hexdigest()
print("baseline daemon.py sha256 = %s" % ORIG_SHA)

MUTANTS = {
    # M1: right symbol, wrong constant -- the tautology the old assertions could not see.
    "M1_wrong_constant": (b"_CREATE_NO_WINDOW = 0x08000000",
                          b"_CREATE_NO_WINDOW = 0x00000008"),
    # M2: the documented anti-pattern -- DETACHED_PROCESS OR'd back in, which Windows
    # resolves by ignoring CREATE_NO_WINDOW.
    "M2_detached_reintroduced": (b"_CREATE_NO_WINDOW = 0x08000000\n",
                                 b"_CREATE_NO_WINDOW = 0x08000000\n_DETACHED_PROCESS = 0x00000008\n"),
}

def run():
    r = subprocess.run([str(PY), "-m", "unittest", "tests.test_daemon_spawn_branch"],
                       cwd=str(TOOLS), capture_output=True, text=True)
    tail = r.stderr.strip().splitlines()
    return r.returncode, tail[-3:] if tail else []

try:
    rc, tail = run()
    print("\n[BASELINE] rc=%d %s" % (rc, " | ".join(tail)))
    for name, (old, new) in MUTANTS.items():
        assert ORIG.count(old) == 1, name
        DAEMON.write_bytes(ORIG.replace(old, new, 1))
        rc, tail = run()
        verdict = "KILLED" if rc != 0 else "SURVIVED <-- test does not discriminate"
        print("[%s] rc=%d %s  => %s" % (name, rc, " | ".join(tail), verdict))
        DAEMON.write_bytes(ORIG)
finally:
    DAEMON.write_bytes(ORIG)
    back = hashlib.sha256(DAEMON.read_bytes()).hexdigest()
    print("\nrestored daemon.py sha256 = %s  (match=%s)" % (back, back == ORIG_SHA))

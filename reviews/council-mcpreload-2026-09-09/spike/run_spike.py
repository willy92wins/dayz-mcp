"""Does a recycled worker serve new code without the client noticing? One session throughout.

The client here plays the host: it opens ONE ClientSession against the supervisor and never
reconnects. If the last call returns the edited value over that same session, the shape the
council converged on works on this machine. If Windows or the SDK chokes on the pipes, this
says so in under a minute and nothing in production was touched.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
IMPL = HERE / "impl.py"
ORIGINAL = IMPL.read_bytes()

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

results = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append((label, ok, detail))
    print(f"  {'OK ' if ok else 'MAL'}  {label}{('  ' + detail) if detail else ''}")


def text_of(result) -> str:
    return "".join(b.text for b in result.content if getattr(b, "type", "") == "text")


async def main() -> int:
    params = StdioServerParameters(
        command=sys.executable, args=["-u", str(HERE / "supervisor.py")], cwd=str(HERE),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("\n=== catalogo ===")
            listed = await session.list_tools()
            names = sorted(t.name for t in listed.tools)
            check("tools/list trae la tool del trabajador", "impl_version" in names, str(names))
            check("tools/list trae la tool del supervisor", "server_reload" in names)

            print("\n=== antes de editar ===")
            first = text_of(await session.call_tool("impl_version", {}))
            check("sirve 'old'", first.startswith("old|"), first)
            worker_pid_before = first.split("pid=")[-1]

            print("\n=== edito el fuente en disco ===")
            IMPL.write_bytes(ORIGINAL.replace(b'VERSION = "old"', b'VERSION = "new"'))
            stale = text_of(await session.call_tool("impl_version", {}))
            check("sigue sirviendo 'old' (es el proceso, no el disco)",
                  stale.startswith("old|"), stale)

            print("\n=== el reciclo drena lo que hay en vuelo ===")
            slow = asyncio.create_task(session.call_tool("slow_echo", {"text": "x", "seconds": 2.0}))
            await asyncio.sleep(0.4)
            started = time.monotonic()
            recycled = text_of(await session.call_tool("server_reload", {}))
            waited = time.monotonic() - started
            slow_text = text_of(await slow)
            payload = json.loads(recycled) if recycled.startswith("{") else {}
            check("el reciclo tuvo exito", payload.get("status") == "recycled", recycled)
            check("espero a la llamada en vuelo", waited >= 1.2, f"espero {waited:.2f}s")
            check("la llamada en vuelo la sirvio el trabajador VIEJO",
                  slow_text.endswith(f"pid={worker_pid_before}"), slow_text)
            check("el trabajador cambio de pid",
                  str(payload.get("new_worker_pid")) != worker_pid_before,
                  f"{worker_pid_before} -> {payload.get('new_worker_pid')}")

            print("\n=== despues del reciclo, MISMA sesion ===")
            after = text_of(await session.call_tool("impl_version", {}))
            check("sirve 'new'", after.startswith("new|"), after)
            # Popen.pid is the venv redirector, not the interpreter that serves the
            # tool: .venv-mcp\Scripts\python.exe re-execs C:\Python314\python.exe.
            # The claim worth checking is that the SERVING pid moved.
            check("y lo sirve otro proceso, no el que servia antes",
                  not after.endswith(f"pid={worker_pid_before}"),
                  f"servia {worker_pid_before}, ahora {after.split('pid=')[-1]}")

            print("\n=== cargas grandes por el pipe (el riesgo que nombro Gemini) ===")
            for kb in (1024, 8192, 32768):
                started = time.monotonic()
                blob = text_of(await session.call_tool("big_payload", {"kilobytes": kb}))
                took = time.monotonic() - started
                check(f"{kb // 1024} MB atraviesan padre e hijo sin bloquearse",
                      len(blob) == kb * 1024, f"{len(blob)} bytes en {took:.2f}s")

            print("\n=== la sesion nunca se reabrio ===")
            again = await session.list_tools()
            check("tools/list sigue respondiendo en la misma sesion",
                  "impl_version" in {t.name for t in again.tools})

    failed = [label for label, ok, _ in results if not ok]
    print("\n" + "=" * 60)
    if failed:
        print(f"VEREDICTO: FALLA. {len(failed)} de {len(results)}: {failed}")
        return 1
    print(f"VEREDICTO: PASA. {len(results)} comprobaciones, todas verdes.")
    return 0


if __name__ == "__main__":
    try:
        code = asyncio.run(main())
    finally:
        IMPL.write_bytes(ORIGINAL)
        print("impl.py restaurado a 'old'")
    raise SystemExit(code)

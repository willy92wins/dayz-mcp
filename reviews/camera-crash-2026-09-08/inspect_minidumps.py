"""Read only: extract exception registers and module identity, never raw strings.

Layouts: Microsoft Learn MINIDUMP_HEADER, MINIDUMP_DIRECTORY,
MINIDUMP_EXCEPTION_STREAM, MINIDUMP_EXCEPTION, MINIDUMP_MODULE and x64 CONTEXT.
No debugger attachment, processes, symbol downloads or writes to the evidence.
"""
from pathlib import Path
import hashlib
import json
import struct


def inspect(path):
    data = path.read_bytes()
    assert data[:4] == b"MDMP", path

    def unpack(fmt, offset):
        return struct.unpack_from("<" + fmt, data, offset)

    count, directory = unpack("II", 8)
    streams = {}
    for i in range(count):
        kind, size, rva = unpack("III", directory + 12 * i)
        assert rva + size <= len(data)
        streams[kind] = (rva, size)
    exception, size = streams[6]
    assert size >= 168
    code, flags, record, address, params, _ = unpack("IIQQII", exception + 8)
    assert params <= 15
    info = unpack("Q" * params, exception + 40)
    context_size, context = unpack("II", exception + 160)
    assert context_size >= 256 and context + context_size <= len(data)
    # x64 CONTEXT: homes 48 + flags/MxCsr 8 + segments/EFlags 16 + debug regs 48.
    assert unpack("I", context + 48)[0] & 0x100000
    rax, rcx, rdx, rbx, rsp, rbp, rsi, rdi = unpack("8Q", context + 120)
    rip = unpack("Q", context + 248)[0]
    assert rip == address, (hex(rip), hex(address))
    module_info = None
    modules, _ = streams[4]
    for i in range(unpack("I", modules)[0]):
        offset = modules + 4 + i * 108
        base, image_size, checksum, timestamp, name_rva = unpack("QIIII", offset)
        if base <= address < base + image_size:
            name_size = unpack("I", name_rva)[0]
            name = data[name_rva + 4 : name_rva + 4 + name_size].decode("utf-16-le")
            module_info = dict(name=name, base=hex(base), size=image_size,
                               offset=hex(address - base), timestamp=timestamp,
                               checksum=checksum)
    code_bytes = None
    if 5 in streams:
        memory, _ = streams[5]
        for i in range(unpack("I", memory)[0]):
            start, memory_size, memory_rva = unpack("QII", memory + 4 + 16 * i)
            if start <= address - 16 and address + 16 <= start + memory_size:
                fault_rva = memory_rva + address - start
                code_bytes = data[fault_rva - 16 : fault_rva + 16].hex(" ")
    return dict(path=str(path.resolve()), bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                exception_code=hex(code), exception_flags=flags,
                address=hex(address), parameters=[hex(x) for x in info],
                rax=hex(rax), rcx=hex(rcx), rip=hex(rip),
                fault_module=module_info, code_16_before_16_at=code_bytes)


if __name__ == "__main__":
    output = Path(__file__).resolve().parent
    profiles = output.parents[2] / "SUB_BRZ_dev/_client/profiles"
    results = [inspect(profiles / f"DayZDiag_x64_2026-09-08_{stamp}.mdmp")
               for stamp in ("14-08-21", "15-00-28")]
    encoded = (json.dumps(results, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    path = output / "minidumps.json"
    path.write_bytes(encoded)
    assert path.read_bytes() == encoded and path.stat().st_size == len(encoded)
    print(encoded.decode("utf-8"))
    print(f"VERIFIED minidumps.json: {len(encoded)} bytes")

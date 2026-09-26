# -*- coding: utf-8 -*-
"""Verify the packed PBO carries the fencing, not just that it packed.

Packing is not compiling, and a PBO that built fine can still contain the wrong
sources. This reads the entry table and compares each staged file byte-for-byte
against what the archive stores.
"""
import hashlib
import os
import struct

import sys
PBO = sys.argv[1] if len(sys.argv) > 1 else ""
SRC = sys.argv[2] if len(sys.argv) > 2 else ""

data = open(PBO, "rb").read()


def read_asciiz(buf, pos):
    end = buf.index(b"\x00", pos)
    return buf[pos:end].decode("utf-8", "replace"), end + 1


pos = 0
entries = []
while True:
    name, pos = read_asciiz(data, pos)
    mime, orig, res, ts, size = struct.unpack_from("<5I", data, pos)
    pos += 20
    if name == "":
        if mime == 0x56657273:  # "Vers" header, properties follow
            while True:
                key, pos = read_asciiz(data, pos)
                if key == "":
                    break
                _val, pos = read_asciiz(data, pos)
            continue
        break
    entries.append({"name": name, "size": size, "orig": orig, "mime": mime})

offset = pos
for e in entries:
    e["data"] = data[offset:offset + e["size"]]
    offset += e["size"]

print("PBO      : %s" % PBO)
print("bytes    : %d" % len(data))
print("entradas : %d" % len(entries))
print()

mismatch = 0
inst_hits = 0
for e in sorted(entries, key=lambda x: x["name"]):
    rel = e["name"].replace("\\", os.sep)
    disk = os.path.join(SRC, rel)
    note = ""
    if os.path.exists(disk):
        raw = open(disk, "rb").read()
        same = hashlib.sha256(raw).digest() == hashlib.sha256(e["data"]).digest()
        note = "identico a fuente" if same else "!! DIFIERE DE LA FUENTE"
        if not same:
            mismatch += 1
    else:
        note = "(sin fichero en el staging)"
    hits = e["data"].count(b"&inst=")
    inst_hits += hits
    flag = ("  inst=x%d" % hits) if hits else ""
    print("  %-52s %8d B  %s%s" % (e["name"], e["size"], note, flag))

print()
print("entradas que difieren de su fuente: %d" % mismatch)
print("ocurrencias de '&inst=' dentro del PBO: %d" % inst_hits)
print("VEREDICTO: %s" % ("OK" if mismatch == 0 and inst_hits >= 4 else "REVISAR"))

from pathlib import Path
import hashlib
ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent

def write(path, content):
    p=Path(path)
    if not p.is_absolute(): p=ROOT/p
    p.parent.mkdir(parents=True,exist_ok=True)
    data=content.encode("utf-8") if isinstance(content,str) else content
    p.write_bytes(data)
    actual=p.read_bytes()
    assert actual == data and p.stat().st_size == len(data), str(p)
    print(f"VERIFIED {p}: {len(actual)} bytes sha256={hashlib.sha256(actual).hexdigest()}")

def backup(relative, ticket):
    p=ROOT/relative
    target=OUT/ticket/(p.name+".BEFORE")
    if not target.exists(): write(target,p.read_bytes())
    return p.read_bytes()

def replace(relative, old, new):
    p=ROOT/relative
    raw=p.read_bytes()
    newline="\r\n" if b"\r\n" in raw else "\n"
    old=old.replace("\r\n","\n").replace("\n",newline).encode()
    new=new.replace("\r\n","\n").replace("\n",newline).encode()
    assert raw.count(old)==1, (relative,raw.count(old))
    write(p,raw.replace(old,new,1))

def done(ticket, summary):
    write(OUT/ticket/"DONE",b"")
    p=OUT/"PROGRESS.md"
    text=p.read_text(encoding="utf-8")
    old=f"- {ticket}: PENDIENTE"
    assert old in text
    write(p,text.replace(old,f"- {ticket}: HECHO ? {summary}"))

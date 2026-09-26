from pathlib import Path
import hashlib, json, sys
sys.stdout.reconfigure(encoding="utf-8")
ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent
manifest=OUT/"source-snapshots.json"
seen=json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
for arg in sys.argv[1:]:
    path, _, span=arg.partition("::")
    p=Path(path)
    if not p.is_absolute(): p=ROOT/p
    b=p.read_bytes(); lines=b.decode("utf-8-sig").splitlines()
    key=str(p.resolve()); h=hashlib.sha256(b).hexdigest()
    old=seen.get(key)
    if old and old["sha256"]!=h: print("DRIFT",key,old["sha256"],h)
    seen[key]={"bytes":len(b),"sha256":h}
    print("SOURCE",key,"bytes",len(b),"sha256",h)
    for part in (span or "1-80").split(","):
        start,_,end=part.partition("-")
        a=int(start); z=int(end or start)
        for i in range(a,min(z,len(lines))+1): print(f"{i}: {lines[i-1]}")
raw=(json.dumps(seen,ensure_ascii=False,indent=2)+"\n").encode("utf-8")
manifest.write_bytes(raw)
assert manifest.read_bytes()==raw

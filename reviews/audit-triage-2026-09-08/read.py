import sys,hashlib,json,datetime
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
root=Path(__file__).resolve().parents[2]
out=Path(__file__).resolve().parent
chunks=[]
for arg in sys.argv[1:]:
 f,a,b=arg.rsplit(":",2);p=root/f;raw=p.read_bytes();lines=raw.decode("utf-8-sig").splitlines()
 chunks.append("\nFILE "+f+" bytes="+str(len(raw))+" sha256="+hashlib.sha256(raw).hexdigest())
 chunks.extend(str(i)+": "+lines[i-1] for i in range(int(a),min(int(b),len(lines))+1))
text="\n".join(chunks)+"\n"
p=out/"evidence.txt";old=p.read_bytes() if p.exists() else b"";data=old+text.encode("utf-8");p.write_bytes(data);assert p.read_bytes()==data
print(text)

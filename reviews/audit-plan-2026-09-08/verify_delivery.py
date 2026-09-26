from pathlib import Path
from collections import Counter
import hashlib, json, re, subprocess, sys
sys.stdout.reconfigure(encoding="utf-8")
ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent
lines=[]
def check(ok, message):
    lines.append(("PASS " if ok else "FAIL ")+message)
    return ok
qa=(OUT/"QA-TRIAJE.md").read_text(encoding="utf-8")
plan=(OUT/"PLAN.md").read_text(encoding="utf-8")
report=(ROOT/"reviews/audit-triage-2026-09-08/REPORT.md").read_text(encoding="utf-8")
data=json.loads((OUT/"coverage.json").read_text(encoding="utf-8"))
entries=re.findall(r"^### ([A-Z][A-Z0-9-]*[ab]?) .*?\n\*\*VEREDICTO: ([A-Z ]+)\.",report,re.M)
live={i for i,v in entries if v=="VIVO"}
flat=[i for ids in data["mapping"].values() for i in ids]
sample=Counter(re.findall(r"^\| (VIVO|FALSO|NO VERIFICABLE) \u00b7 ",qa,re.M))
check(sample=={"VIVO":4,"FALSO":4,"NO VERIFICABLE":4},"QA sample 4 VIVO + 4 FALSO + 4 NO VERIFICABLE")
check(qa.count("**COINCIDO.**")==12,"QA 12 explicit judgments")
check(set(flat)==live and len(flat)==len(live)==60,"60 live entries covered exactly once")
units=re.findall(r"^### ([HDN]\d+) ",plan,re.M)
check(Counter(u[0] for u in units)=={"H":2,"D":4,"N":16} and len(units)==len(set(units)),"22 decision units: H=2 D=4 N=16")
check(re.findall(r"^## (.+)$",plan,re.M)==["HACER","DECIDIR","NO HACER"],"exactly three plan categories")
for unit,ids in data["mapping"].items():
    check(f"| {unit} | "+", ".join(ids)+" |" in plan,"mapping row "+unit)
check(len(data["props"])==40 and Counter(p[0].split("-")[0] for p in data["props"])=={"6.1":16,"6.2":10,"6.3":6,"7":8},"40 proposal components 16/10/6/8")
check(all("| "+p[0]+" | "+p[1]+" | "+p[2]+" | "+p[3]+" |" in plan for p in data["props"]),"proposal table matches decisions manifest")
for match in re.finditer(r"^### ([HDN]\d+) .*?(?=^### |\Z)",plan,re.M|re.S):
    unit,body=match.group(1),match.group(0)
    check(all(token in body for token in ("QU\u00c9","POR QU\u00c9","COSTE","RIESGO","QUE LO DA POR HECHO","DEPENDENCIAS")),unit+" required decision fields")
cites=re.findall(r"(\.{2}/scripts/[A-Za-z0-9_./-]+|(?:tools|addon)/[A-Za-z0-9_./-]+|product-spec\.md|PROJECT-MAP\.md|GATES\.md):(\d+)",qa+"\n"+plan)
invalid=[]
for rel,number in cites:
    path=ROOT/rel
    if not path.is_file() or not 1<=int(number)<=len(path.read_bytes().splitlines()):
        invalid.append((rel,number))
check(not invalid,f"citation paths and bounds: {len(cites)} checked, invalid={invalid}")
sources=json.loads((OUT/"source-snapshots.json").read_text(encoding="utf-8"))
drift=[]
for path,meta in sources.items():
    try: raw=Path(path).read_bytes()
    except OSError as exc: drift.append((path,type(exc).__name__)); continue
    if len(raw)!=meta["bytes"] or hashlib.sha256(raw).hexdigest()!=meta["sha256"]:
        drift.append((path,"changed"))
check(not drift,f"source snapshots stable: {len(sources)} checked, drift={drift}")
inv=json.loads((OUT/"inventory.json").read_text(encoding="utf-8"))
head=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
staged=subprocess.check_output(["git","diff","--cached","--name-status"],cwd=ROOT,text=True).strip()
check(head==inv["head"],"HEAD unchanged since inventory")
check(staged==inv["staged"],"staged path list unchanged since inventory")
for name in ("QA-TRIAJE.md","PLAN.md"):
    raw=(OUT/name).read_bytes()
    check(not raw.startswith(b"\xef\xbb\xbf") and "\ufffd" not in raw.decode("utf-8"),name+" UTF-8 without BOM/replacement character")
failed=sum(line.startswith("FAIL ") for line in lines)
lines += [f"DOCUMENT_CHECKS={len(lines)} FAIL={failed}",f"Exit code: {int(bool(failed))}"]
raw=("\n".join(lines)+"\n").encode("utf-8")
target=OUT/"validation.txt"; target.write_bytes(raw); assert target.read_bytes()==raw
print(raw.decode("utf-8"),end="")
sys.exit(int(bool(failed)))

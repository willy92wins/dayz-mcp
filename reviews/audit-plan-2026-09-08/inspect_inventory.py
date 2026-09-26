from pathlib import Path
from collections import Counter
import ast, hashlib, json, re, subprocess, sys
sys.stdout.reconfigure(encoding="utf-8")
ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent
report=(ROOT/"reviews/audit-triage-2026-09-08/REPORT.md").read_text(encoding="utf-8")
entries=re.findall(r"^### ([A-Z][A-Z0-9-]*[ab]?) .*?\n\*\*VEREDICTO: ([A-Z ]+)\.",report,re.M)
assert len(entries)==103, len(entries)
counts=dict(Counter(v for _,v in entries))
live=[i for i,v in entries if v=="VIVO"]
assert len(live)==60 and len(set(live))==60
original=(ROOT/"AUDITORIA_MCP_2026-09-07.md").read_text(encoding="utf-8").split("## 2. ")[1].split("## 6. ")[0]
ids=set(re.findall(r"(?:E|P|T)-(?:[A-Z]+-\d\d|P\d\d)",original))
assert len(ids)==79,len(ids)
tests=sorted((ROOT/"tools/tests").glob("test_*.py"))
imports=[]
for p in tests:
    tree=ast.parse(p.read_text(encoding="utf-8-sig"),filename=str(p))
    for n in ast.walk(tree):
        if isinstance(n,ast.ImportFrom) and n.module and n.module.startswith("tests.test_"):
            imports.append({"file":p.relative_to(ROOT).as_posix(),"line":n.lineno,"module":n.module,"names":[a.name for a in n.names]})
        elif isinstance(n,ast.Import):
            for a in n.names:
                if a.name.startswith("tests.test_"):
                    imports.append({"file":p.relative_to(ROOT).as_posix(),"line":n.lineno,"module":a.name,"names":[]})
task7={p.name:len(p.read_bytes().splitlines()) for p in tests if p.name in {
"test_task7_review_regressions.py","test_task7_rereview_regressions.py",
"test_task7_final_authority_regressions.py","test_task7_final_lifecycle_regressions.py"}}
result={"head":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
"entry_count":len(entries),"verdict_counts":counts,"original_id_count":len(ids),
"live_ids":live,"test_module_count":len(tests),"test_to_test_imports":imports,
"task7_lines":task7,"backups":[p.name for p in sorted((ROOT/"tools/tests").glob("*.bak*"))],
"staged":subprocess.check_output(["git","diff","--cached","--name-status"],cwd=ROOT,text=True).strip()}
raw=(json.dumps(result,ensure_ascii=False,indent=2)+"\n").encode("utf-8")
p=OUT/"inventory.json"; p.write_bytes(raw); assert p.read_bytes()==raw
print("TRIAGE_ENTRIES",len(entries))
print("VERDICTS",json.dumps(counts,sort_keys=True))
print("ORIGINAL_IDS",len(ids))
print("TEST_MODULES",len(tests))
print("TEST_TO_TEST_IMPORTS",len(imports))
print("TASK7_LINES",json.dumps(task7,sort_keys=True),"TOTAL",sum(task7.values()))
print("BACKUPS",len(result["backups"]))
print("STAGED",result["staged"])
print("HEAD",result["head"])
print("INVENTORY_WRITE_VERIFIED",len(raw),hashlib.sha256(raw).hexdigest())

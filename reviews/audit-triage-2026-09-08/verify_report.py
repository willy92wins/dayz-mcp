from pathlib import Path
import re,hashlib,sys,collections
sys.stdout.reconfigure(encoding="utf-8")
root=Path(__file__).resolve().parents[2];out=Path(__file__).resolve().parent
report=(out/"REPORT.md").read_text(encoding="utf-8")
audit=(root/"AUDITORIA_MCP_2026-09-07.md").read_text(encoding="utf-8").split("## 6.")[0]
ids=set(re.findall(r"\b[EPT]-(?:TICK|DUP|HOT|BUF|ERR|GUI|ARC|OPT|CON)-\d+\b|\b[EPT]-P\d+\b",audit))
missing=sorted(i for i in ids if not re.search(r"^### "+i+r"(?:[ab])?\b",report,re.M))
cites=sorted(set((f,int(n)) for f,n in re.findall(r"`([A-Za-z_./][A-Za-z0-9_./-]*\.(?:py|ps1|md|c|cpp|layout|json)):(\d+)`",report)))
evidence=(out/"evidence.txt").read_text(encoding="utf-8");seen={};opened=set();active=None
for l in evidence.splitlines():
 m=re.match(r"FILE (.+) bytes=(\d+) sha256=([a-f0-9]+)",l)
 if m:active=m[1];seen[active]=m[3]
 elif active and (m:=re.match(r"(\d+):",l)):opened.add((active,int(m[1])))
rows=[];new=[];bad=[]
for f,n in cites:
 p=root/f
 if not p.is_file():bad.append(f"missing {f}:{n}");continue
 lines=p.read_text(encoding="utf-8-sig").splitlines()
 if not 1<=n<=len(lines):bad.append(f"out_of_range {f}:{n}");continue
 row=f"{f}:{n}: {lines[n-1]}";rows.append(row)
 if (f,n) not in opened:new.append(row)
changed=[]
for f,h in seen.items():
 p=root/f
 if p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest()!=h:changed.append(f)
for name,data in [("citations.txt","\n".join(rows)+"\n"),("citation-new.txt","\n".join(new)+"\n")]:
 raw=data.encode("utf-8");p=out/name;p.write_bytes(raw);assert p.read_bytes()==raw
counts=collections.Counter(re.findall(r"\*\*VEREDICTO: (VIVO|YA ARREGLADO|MOVIDO|FALSO|NO VERIFICABLE)\.\*\*",report))
print(f"audit_ids={len(ids)} missing={len(missing)} {missing}")
print(f"verdict_entries={sum(counts.values())} counts={dict(counts)}")
print(f"explicit_unique_citations={len(cites)} invalid={len(bad)} {bad}")
print(f"source_files_with_recorded_hash={len(seen)} changed_since_last_open={len(changed)} {changed}")
print(f"citations_opened_now_not_in_range_log={len(new)}")
print("report_bytes="+str((out/"REPORT.md").stat().st_size))
print("CHECK_SCOPE=report completeness, citation existence and source drift; NOT semantic proof or product test")
sys.exit(1 if missing or bad or changed else 0)

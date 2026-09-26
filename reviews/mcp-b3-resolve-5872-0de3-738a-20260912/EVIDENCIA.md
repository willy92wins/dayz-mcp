# EVIDENCIA mcp-b3-resolve-5872-0de3-738a-20260912

platform: orq-b3-close-20260912
NO GATES.md v9 / gates/inbox-* / pack PBO / 3fc1.

## Sources (Temp JobDirs)
- Live retest2: `Temp/orq-dispatch/mcp-b3-5872-retest2-20260912` (INDEX+out+POST; RC PASS)
- PBO2 deploy: `Temp/orq-dispatch/mcp-b3-pbo2-5872-20260912` (INDEX equality 738a ok)
- Earlier live seated 0de3: `Temp/orq-dispatch/mcp-b3-0de3-5872-live-agy-20260911` (cited; confirmed again in retest2 seated=PASS)

## PBO / tip
- Workshop=Mods SHA: `F82CFA8C4E557FFF0041661EC106DB6CF0ACA96C664515518DCC8459511E92AE`
- tip: `a9fe673ad9beddf83887933bc742fa046c96b22f` (PR#23 seated-camera PBO2)
- equality 738a: construido==desplegado (PBO2 INDEX)

## fb-20260909-222257-5872 — live PASS camera seated post PBO2
- RC PASS; seated=PASS; 5872=PASS; dec6=PASS; restore=PASS
- no permanent `camera_unavailable_vehicle`
- camera_set=`camera_unmoved_cabin`; camera_get view=vehicle
- evidence JobDir: Temp/orq-dispatch/mcp-b3-5872-retest2-20260912

## fb-20260830-002237-0de3 — live PASS seated
- Earlier live PASS seated OffroadHatchback; confirmed retest2 seated=PASS under PBO2
- evidence: Temp/orq-dispatch/mcp-b3-5872-retest2-20260912 (+ prior live JobDirs)

## fb-20260908-172018-738a — CLOSE equality construido==desplegado
- PBO2 INDEX: equality 738a ok; Workshop=Mods F82CFA8C… tip a9fe673
- NO new pack hop
- evidence: Temp/orq-dispatch/mcp-b3-pbo2-5872-20260912

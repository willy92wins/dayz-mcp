# FASE0-CONCURRENCIA — presupuesto offline de script arena por mod

Generado UTC: `2026-07-27T19:47:17.545001+00:00`

> Resultado: tabla completa de los 418 directorios observados (417 aliases `@Mod` + 1 sentinel).  
> Hay estimación utilizable para 408 aliases; 9 quedan fail-closed como desconocidos (8 timeouts y `@LFQuad3` ilegible).

## Método exacto

- Herramienta sin modificar: `refs/measure_mod_scripts.ps1` (SHA-256 `83cfb6c8c6e59ddbf4058144584e234531e9313375495cfb7f2157461ca4ee68`). Su lector de índice está en `refs/measure_mod_scripts.ps1:15-49`; el scan/handling de índices ilegibles en `:55-61`; el agregado `4_World` en `:78-88`.
- Ejecución requerida: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\guill\AppData\Local\Temp\dayz-mcp-p2-fase0\refs\measure_mod_scripts.ps1" -ModsRoot "C:\Program Files (x86)\Steam\steamapps\common\DayZ\!Workshop" -OutCsv "C:\Users\guill\AppData\Local\Temp\dayz-mcp-p2-fase0\fase0\.work\mod_scripts_raw.csv"`. Exit code `0`, duración `7.098` s.
- La ejecución requerida encontró 68 PBOs, 28 mods con algún `.c` y 10.417 KB (formato regional: 10 417 KB) de fuente `4_World`, pero no siguió 372 junctions de PowerShell 5.
- Recuperación conservadora: el mismo script se invocó por cada junction. 364 cerraron con `TOTAL` + CSV y stderr vacío; 8 superaron 120 s y se marcan `UNKNOWN_TIMEOUT`. No se reimplementó ni se extrajo ningún PBO.
- Los 45 aliases físicos proceden de la ejecución requerida. `@LFQuad3` queda `UNKNOWN_UNREADABLE_INDEX`; las otras ausencias de CSV son `SCAN_OK_NO_C_SOURCE`, no números inventados.

## Modelo y margen de error

- Cap de static memory por módulo: **33.554 kB**. Ratio central para `4_World`: **0,65 kB static / KB fuente**, según tres boots A/B'/C del proyecto `lfpowergrid-compile-footprint` y la constante citada por la herramienta (`refs/measure_mod_scripts.ps1:6-8,88`).
- Margen atribuido al ratio: **±30 % de ingeniería**, es decir **0,455–0,845**. No es un intervalo estadístico: se elige por la muestra de solo tres boots de un único proyecto y la extrapolación entre mezclas de scripts.
- La herramienta redondea cada `.c` a 0,1 KB y luego el total a 1 KB. Banda conservadora de redondeo: `±(0,05 × Files + 0,5) KB`; `Files` incluye todos los módulos, por lo que sobreestima el error de `4_World`.
- Fórmulas: central `World_KB × 0,65`; inferior `max(0, World_KB − redondeo) × 0,455`; superior `(World_KB + redondeo) × 0,845`.
- **No es medición exacta de arena.** La exacta requiere arrancar servidor y leer líneas `Module:` del script-log, prohibido en esta sesión. Tampoco existe ratio verificado para `1_Core`, `2_GameLib`, `3_Game` o `5_Mission`: sus columnas son fuente, no static arena.
- `0` significa “ningún `.c`/`4_World` detectado en índices legibles”, no “el stack total ocupa cero”; no incluye vanilla/base ni interacciones de compilación. Sumar aliases duplicados puede contar dos veces el mismo Workshop ID.

## Cobertura

| Estado | Filas | Uso |
|---|---:|---|
| `ESTIMATED_OFFLINE` | 345 | usable |
| `NOT_A_MOD_DIRECTORY` | 1 | sentinel |
| `SCAN_OK_NO_C_SOURCE` | 63 | usable |
| `UNKNOWN_TIMEOUT` | 8 | fail-closed |
| `UNKNOWN_UNREADABLE_INDEX` | 1 | fail-closed |

## Top 20 por cota superior `4_World`

| Mod | World fuente KB | Est. static kB | Banda kB | % cap central / superior |
|---|---:|---:|---:|---:|
| @FrontPack2.0 | 4733 | 3076.5 | 0.0–11425.4 | 9.17 / 34.05 |
| @LaFronteraCherno | 4574 | 2973.1 | 0.0–10929.4 | 8.86 / 32.57 |
| @BANOVLF-1 | 4386 | 2850.9 | 0.0–10690.0 | 8.50 / 31.86 |
| @FrontCars | 1220 | 793.0 | 0.0–9126.7 | 2.36 / 27.20 |
| @Frontera Cars | 4988 | 3242.2 | 414.2–7660.5 | 9.66 / 22.83 |
| @FrontIsle | 4175 | 2713.8 | 308.6–6482.5 | 8.09 / 19.32 |
| @BANOVLF-2 | 4374 | 2843.1 | 630.6–6220.9 | 8.47 / 18.54 |
| @downbad serverpack main | 4417 | 2871.1 | 876.0–5837.9 | 8.56 / 17.40 |
| @Skullzone.S.P | 1598 | 1038.7 | 0.0–5670.7 | 3.10 / 16.90 |
| @DobermannPack | 1600 | 1040.0 | 0.0–5498.9 | 3.10 / 16.39 |
| @DayZ-Expansion-Bundle | 5869 | 3814.8 | 2629.3–5035.6 | 11.37 / 15.01 |
| @Pack-GMZ-5 | 4340 | 2821.0 | 1924.9–3759.8 | 8.41 / 11.21 |
| @LFTEST | 4153 | 2699.5 | 1840.6–3600.3 | 8.05 / 10.73 |
| @Deadline | 4161 | 2704.7 | 1864.0–3570.4 | 8.06 / 10.64 |
| @Nemsis Craftingpack All in One | 3700 | 2405.0 | 1670.0–3151.6 | 7.17 / 9.39 |
| @Hollow Test | 1257 | 817.1 | 0.0–2842.2 | 2.44 / 8.47 |
| @FrontCherno2035 | 532 | 345.8 | 0.0–2587.1 | 1.03 / 7.71 |
| @28addons | 2387 | 1551.5 | 784.9–2576.3 | 4.62 / 7.68 |
| @LF1.1 | 1805 | 1173.2 | 351.8–2397.1 | 3.50 / 7.14 |
| @FC_Fish_Equip | 341 | 221.7 | 0.0–2294.2 | 0.66 / 6.84 |

## Q1–Q3 adjudicadas para las fases siguientes

- **Q1 — TTL de SHARED:** muere tras **120 s sin participantes**, configurable; no permanece indefinidamente. Se reutiliza el TTL de identidad ya verificado en el spec para evitar una constante temporal nueva sin evidencia.
- **Q2 — EXCLUSIVE:** puede desalojar un SHARED **solo con 0 participantes**; con ≥1 participante espera. El desalojo nunca precede al check de participantes.
- **Q3 — presupuesto:** tabla congelada, no medición por drain. El guard usa la **cota superior**, falla cerrado ante `UNKNOWN_*` y exige regenerar la tabla cuando cambien los PBOs. Esta Fase 0 no calculó hashes de contenido PBO; por tanto la detección automática de staleness sigue pendiente y el artefacto es una foto fechada, no una autoridad perpetua.

Estas decisiones cierran el punto 3 de `refs/2026-07-26-multi-agent-run-sharing-plan.md:57-60` y las preguntas de `refs/2026-07-26-multi-agent-run-sharing-spec.md:282-289` sin tocar código.

## Tabla completa

| Mod | Tipo | Workshop ID | Estado | PBOs | World KB | Game KB | Mission KB | Other KB | .c | Est. 4W kB | Banda 4W kB | % cap est./sup. |
|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| !DO_NOT_CHANGE_FILES_IN_THESE_FOLDERS | sentinel | — | `NOT_A_MOD_DIRECTORY` | 0 | — | — | — | — | — | — | — | — |
| @28 Dayz Later KOTH | junction | 3451972517 | `SCAN_OK_NO_C_SOURCE` | 0 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @28addons | junction | 3033197104 | `ESTIMATED_OFFLINE` | 82 | 2387 | 517 | 166 | 243 | 13228 | 1551.5 | 784.9–2576.3 | 4.62 / 7.68 |
| @28DIAS-dev | junction | 3616490793 | `ESTIMATED_OFFLINE` | 18 | 753 | 1 | 1 | 0 | 326 | 489.4 | 335.0–650.5 | 1.46 / 1.94 |
| @28DIAS-Z | junction | 3031010953 | `ESTIMATED_OFFLINE` | 27 | 1227 | 13 | 23 | 6 | 630 | 797.6 | 543.7–1063.9 | 2.38 / 3.17 |
| @3D Printer | junction | 3363783478 | `ESTIMATED_OFFLINE` | 2 | 56 | 7 | 1 | 0 | 18 | 36.4 | 24.8–48.5 | 0.11 / 0.14 |
| @4KBOSSKVehiclePackage | junction | 3387855369 | `ESTIMATED_OFFLINE` | 47 | 794 | 1 | 0 | 0 | 173 | 516.1 | 357.1–678.7 | 1.54 / 2.02 |
| @[CnG]UAZ_452 | junction | 3359682865 | `SCAN_OK_NO_C_SOURCE` | 0 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @[CrSk] BMW 525i E34 | junction | 1869021368 | `SCAN_OK_NO_C_SOURCE` | 0 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @[Remastered] Arma Weapon Pack | junction | 1793351435 | `SCAN_OK_NO_C_SOURCE` | 0 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @A-6 Secure Containers | junction | 3123910249 | `ESTIMATED_OFFLINE` | 2 | 9 | 2 | 2 | 0 | 8 | 5.9 | 3.7–8.4 | 0.02 / 0.02 |
| @A6 Virtual Storage Compatibility Addon (VSM) | physical | — | `SCAN_OK_NO_C_SOURCE` | 0 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @A6_AnimRTTest | physical | — | `ESTIMATED_OFFLINE` | 1 | 2 | 0 | 2 | 0 | 2 | 1.3 | 0.6–2.2 | 0.00 / 0.01 |
| @A6_MK47 | physical | — | `ESTIMATED_OFFLINE` | 1 | 0 | 0 | 0 | 0 | 1 | 0.0 | 0.0–0.5 | 0.00 / 0.00 |
| @A6_SR2M | physical | — | `ESTIMATED_OFFLINE` | 1 | 5 | 0 | 0 | 0 | 2 | 3.2 | 2.0–4.7 | 0.01 / 0.01 |
| @A6_SR2M_deps | physical | — | `ESTIMATED_OFFLINE` | 12 | 450 | 6 | 103 | 0 | 195 | 292.5 | 200.1–388.9 | 0.87 / 1.16 |
| @A6_TestPack | physical | — | `ESTIMATED_OFFLINE` | 13 | 477 | 6 | 103 | 0 | 213 | 310.1 | 212.0–412.5 | 0.92 / 1.23 |
| @AdditionalMedicSupplies | junction | 2579252958 | `ESTIMATED_OFFLINE` | 2 | 96 | 0 | 5 | 0 | 90 | 62.4 | 41.4–85.3 | 0.19 / 0.25 |
| @Admirals Head Shot Mod | junction | 3170819522 | `ESTIMATED_OFFLINE` | 1 | 5 | 0 | 0 | 0 | 2 | 3.2 | 2.0–4.7 | 0.01 / 0.01 |
| @Admirals NVG Mod | junction | 3416025947 | `ESTIMATED_OFFLINE` | 1 | 5 | 11 | 8 | 0 | 13 | 3.2 | 1.8–5.2 | 0.01 / 0.02 |
| @Admirals Parachute Mod | junction | 2912241382 | `ESTIMATED_OFFLINE` | 1 | 55 | 9 | 6 | 0 | 23 | 35.8 | 24.3–47.9 | 0.11 / 0.14 |
| @Advanced Spawn - Expansion | junction | 3475184760 | `ESTIMATED_OFFLINE` | 1 | 9 | 3 | 17 | 0 | 808 | 5.9 | 0.0–42.2 | 0.02 / 0.13 |
| @Advanced Weapon Scopes | junction | 2143128974 | `ESTIMATED_OFFLINE` | 1 | 261 | 0 | 0 | 0 | 30 | 169.7 | 117.8–222.2 | 0.51 / 0.66 |
| @AdvancedBanking V2 | junction | 3065185420 | `ESTIMATED_OFFLINE` | 1 | 177 | 42 | 1 | 0 | 47 | 115.0 | 79.2–152.0 | 0.34 / 0.45 |
| @AgricultureCore | junction | 3690289718 | `ESTIMATED_OFFLINE` | 1 | 28 | 6 | 4 | 0 | 19 | 18.2 | 12.1–24.9 | 0.05 / 0.07 |
| @AI Bandits | junction | 3628006769 | `ESTIMATED_OFFLINE` | 1 | 166 | 13 | 1 | 0 | 29 | 107.9 | 74.6–141.9 | 0.32 / 0.42 |
| @Airdrop-Upgraded | junction | 1870524790 | `ESTIMATED_OFFLINE` | 2 | 208 | 4 | 0 | 0 | 12 | 135.2 | 94.1–176.7 | 0.40 / 0.53 |
| @AirRaid | junction | 2065443797 | `ESTIMATED_OFFLINE` | 1 | 68 | 9 | 1 | 0 | 22 | 44.2 | 30.2–58.8 | 0.13 / 0.18 |
| @AJs Creatures V2 | junction | 3413364741 | `ESTIMATED_OFFLINE` | 1 | 5 | 0 | 0 | 0 | 3 | 3.2 | 2.0–4.8 | 0.01 / 0.01 |
| @Alcohol Production | junction | 3452807884 | `ESTIMATED_OFFLINE` | 2 | 7 | 1 | 9 | 109 | 2110 | 4.5 | 0.0–95.5 | 0.01 / 0.28 |
| @Alevaric's Clothing Overhaul | junction | 3354931239 | `ESTIMATED_OFFLINE` | 6 | 78 | 0 | 0 | 0 | 34 | 50.7 | 34.5–67.8 | 0.15 / 0.20 |
| @AlienSkinning | junction | 3404057000 | `ESTIMATED_OFFLINE` | 1 | 2 | 3 | 1 | 0 | 6 | 1.3 | 0.5–2.4 | 0.00 / 0.01 |
| @Alteria | junction | 3296994216 | `ESTIMATED_OFFLINE` | 19 | 32 | 2 | 0 | 0 | 20 | 20.8 | 13.9–28.3 | 0.06 / 0.08 |
| @Ambient Animals Pack | junction | 3114410963 | `ESTIMATED_OFFLINE` | 8 | 63 | 4 | 0 | 0 | 40 | 41.0 | 27.5–55.3 | 0.12 / 0.16 |
| @AmmoStackBullet | junction | 2832884779 | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @AnimatedDynamicHelicopters | junction | 3382463948 | `ESTIMATED_OFFLINE` | 1 | 3 | 1 | 0 | 191 | 3448 | 2.0 | 0.0–148.6 | 0.01 / 0.44 |
| @Arma 2 Helicopters Remastered | junction | 2651195301 | `ESTIMATED_OFFLINE` | 19 | 131 | 0 | 0 | 0 | 24 | 85.2 | 58.8–112.1 | 0.25 / 0.33 |
| @ArmA 2 Props Pack | junction | 2160294330 | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @AutoCarCover | junction | 3489483195 | `ESTIMATED_OFFLINE` | 1 | 17 | 7 | 1 | 0 | 7 | 11.1 | 7.3–15.1 | 0.03 / 0.04 |
| @AutoCarFlip | junction | 3422227460 | `ESTIMATED_OFFLINE` | 1 | 4 | 1 | 5 | 0 | 7 | 2.6 | 1.4–4.1 | 0.01 / 0.01 |
| @Automated Turrets | junction | 2851374375 | `ESTIMATED_OFFLINE` | 2 | 28 | 16 | 2 | 0 | 11 | 18.2 | 12.3–24.5 | 0.05 / 0.07 |
| @Autorun Mod | junction | 2313173630 | `ESTIMATED_OFFLINE` | 1 | 1 | 0 | 3 | 0 | 2 | 0.7 | 0.2–1.4 | 0.00 / 0.00 |
| @BallerZ Gear | junction | 3025712002 | `ESTIMATED_OFFLINE` | 32 | 22 | 0 | 0 | 0 | 12 | 14.3 | 9.5–19.5 | 0.04 / 0.06 |
| @Banov | junction | 2415195639 | `ESTIMATED_OFFLINE` | 42 | 147 | 54 | 49 | 2 | 92 | 95.5 | 64.6–128.5 | 0.28 / 0.38 |
| @BANOVLF-1 | junction | 3383963290 | `ESTIMATED_OFFLINE` | 133 | 4386 | 1240 | 2867 | 2088 | 165288 | 2850.9 | 0.0–10690.0 | 8.50 / 31.86 |
| @BANOVLF-2 | junction | 3383963790 | `ESTIMATED_OFFLINE` | 234 | 4374 | 33 | 1 | 2 | 59750 | 2843.1 | 630.6–6220.9 | 8.47 / 18.54 |
| @Banshee Quad | junction | 3747466823 | `ESTIMATED_OFFLINE` | 1 | 12 | 0 | 0 | 0 | 5 | 7.8 | 5.1–10.8 | 0.02 / 0.03 |
| @BaseBuildingPlus | junction | 1710977250 | `ESTIMATED_OFFLINE` | 2 | 423 | 10 | 24 | 0 | 176 | 274.9 | 188.2–365.3 | 0.82 / 1.09 |
| @Basic_Territories_Updated | junction | 2999534116 | `ESTIMATED_OFFLINE` | 8 | 39 | 12 | 7 | 0 | 28 | 25.4 | 16.9–34.6 | 0.08 / 0.10 |
| @BBP_Well_Fix | junction | 2983611236 | `ESTIMATED_OFFLINE` | 1 | 1 | 0 | 0 | 0 | 1 | 0.7 | 0.2–1.3 | 0.00 / 0.00 |
| @BBPItemPack | junction | 2794690371 | `ESTIMATED_OFFLINE` | 1 | 180 | 2 | 6 | 0 | 94 | 117.0 | 79.5–156.5 | 0.35 / 0.47 |
| @Bed-Respawning | junction | 2111275052 | `ESTIMATED_OFFLINE` | 1 | 25 | 0 | 1 | 0 | 7 | 16.2 | 11.0–21.8 | 0.05 / 0.07 |
| @Better Batteries | junction | 3146753440 | `ESTIMATED_OFFLINE` | 1 | 0 | 0 | 0 | 9 | 204 | 0.0 | 0.0–9.0 | 0.00 / 0.03 |
| @Better HUD | junction | 3649982364 | `ESTIMATED_OFFLINE` | 1 | 0 | 0 | 0 | 8 | 126 | 0.0 | 0.0–5.7 | 0.00 / 0.02 |
| @BetterVendingMachines_Expansion | junction | 2777701910 | `ESTIMATED_OFFLINE` | 1 | 0 | 0 | 0 | 0 | 1 | 0.0 | 0.0–0.5 | 0.00 / 0.00 |
| @Bitterroot | junction | 2906823750 | `ESTIMATED_OFFLINE` | 141 | 100 | 2 | 0 | 0 | 78 | 65.0 | 43.5–88.2 | 0.19 / 0.26 |
| @Blackouts Scorpion | junction | 2909935986 | `ESTIMATED_OFFLINE` | 2 | 56 | 0 | 0 | 0 | 21 | 36.4 | 24.8–48.6 | 0.11 / 0.14 |
| @BLR Portable Houses Upgraded | junction | 3673762621 | `ESTIMATED_OFFLINE` | 2 | 307 | 4 | 6 | 0 | 72 | 199.6 | 137.8–262.9 | 0.59 / 0.78 |
| @BodyBags | junction | 2819373632 | `ESTIMATED_OFFLINE` | 2 | 6 | 0 | 0 | 0 | 5 | 3.9 | 2.4–5.7 | 0.01 / 0.02 |
| @BoomLay's Things | junction | 2860643107 | `ESTIMATED_OFFLINE` | 15 | 308 | 12 | 11 | 0 | 173 | 200.2 | 136.0–268.0 | 0.60 / 0.80 |
| @Bottle Cap Collectables | junction | 3005892409 | `ESTIMATED_OFFLINE` | 1 | 0 | 0 | 0 | 0 | 1 | 0.0 | 0.0–0.5 | 0.00 / 0.00 |
| @Breachingcharge | junction | 1827241477 | `ESTIMATED_OFFLINE` | 1 | 147 | 0 | 0 | 0 | 22 | 95.5 | 66.2–125.6 | 0.28 / 0.37 |
| @Breachingcharge Codelock Compatibility | junction | 2464098674 | `ESTIMATED_OFFLINE` | 1 | 1 | 0 | 0 | 0 | 1 | 0.7 | 0.2–1.3 | 0.00 / 0.00 |
| @BreachingCharge_Remastered | junction | 2959987085 | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @BS HackedCrate | junction | 3482229348 | `ESTIMATED_OFFLINE` | 1 | 318 | 2 | 50 | 0 | 19 | 206.7 | 144.0–269.9 | 0.62 / 0.80 |
| @BS KeyRoom | junction | 3514469093 | `ESTIMATED_OFFLINE` | 3 | 556 | 33 | 44 | 0 | 52 | 361.4 | 251.6–472.4 | 1.08 / 1.41 |
| @BuilderItems | junction | 1565871491 | `ESTIMATED_OFFLINE` | 38 | 2 | 0 | 0 | 0 | 2 | 1.3 | 0.6–2.2 | 0.00 / 0.01 |
| @Building Fortifications | junction | 2670506982 | `ESTIMATED_OFFLINE` | 2 | 155 | 15 | 1 | 0 | 37 | 100.8 | 69.5–133.0 | 0.30 / 0.40 |
| @BuildingsMegaModPack | junction | 2307297070 | `SCAN_OK_NO_C_SOURCE` | 4 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @BuildingsModPack6 | junction | 2502755029 | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @BuildingsModPack7 | junction | 2762521201 | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @CannabisPlus | junction | 1932611410 | `ESTIMATED_OFFLINE` | 1 | 135 | 15 | 9 | 0 | 58 | 87.8 | 59.9–116.9 | 0.26 / 0.35 |
| @Car PDA | junction | 3677698666 | `ESTIMATED_OFFLINE` | 1 | 1 | 1 | 0 | 40 | 657 | 0.7 | 0.0–29.0 | 0.00 / 0.09 |
| @CarCover | junction | 2303483532 | `ESTIMATED_OFFLINE` | 2 | 31 | 27 | 0 | 0 | 15 | 20.2 | 13.5–27.3 | 0.06 / 0.08 |
| @CarPack | junction | 3443311258 | `ESTIMATED_OFFLINE` | 58 | 43 | 0 | 0 | 1227 | 280 | 27.9 | 13.0–48.6 | 0.08 / 0.14 |
| @CBTONFORIN | junction | 3344148598 | `ESTIMATED_OFFLINE` | 5 | 6 | 12 | 0 | 0 | 8 | 3.9 | 2.3–5.8 | 0.01 / 0.02 |
| @CF | junction | 1559212036 | `ESTIMATED_OFFLINE` | 3 | 77 | 198 | 12 | 116 | 171 | 50.1 | 30.9–72.7 | 0.15 / 0.22 |
| @CF_CodexW1 | physical | — | `ESTIMATED_OFFLINE` | 3 | 77 | 198 | 12 | 116 | 171 | 50.1 | 30.9–72.7 | 0.15 / 0.22 |
| @Chernarus2035 | junction | 3638582966 | `ESTIMATED_OFFLINE` | 15 | 13 | 1 | 0 | 0 | 4 | 8.5 | 5.6–11.6 | 0.03 / 0.03 |
| @Chiemsee | junction | 1580589252 | `ESTIMATED_OFFLINE` | 34 | 32 | 6 | 0 | 0 | 29 | 20.8 | 13.7–28.7 | 0.06 / 0.09 |
| @CJ LootChests Backpacks Addon | junction | 2994594944 | `ESTIMATED_OFFLINE` | 1 | 3 | 0 | 0 | 0 | 1 | 2.0 | 1.1–3.0 | 0.01 / 0.01 |
| @CJ187-LootChest | junction | 2345073965 | `ESTIMATED_OFFLINE` | 1 | 55 | 20 | 1 | 0 | 20 | 35.8 | 24.3–47.7 | 0.11 / 0.14 |
| @CJ187-PokemonCards | junction | 2851058261 | `ESTIMATED_OFFLINE` | 1 | 302 | 0 | 0 | 0 | 17 | 196.3 | 136.8–256.3 | 0.59 / 0.76 |
| @CJ187-SimpleSpawner | junction | 2868802243 | `ESTIMATED_OFFLINE` | 1 | 3 | 4 | 0 | 0 | 5 | 2.0 | 1.0–3.2 | 0.01 / 0.01 |
| @Cl0ud's Military Gear | junction | 1630943713 | `ESTIMATED_OFFLINE` | 9 | 515 | 0 | 0 | 0 | 135 | 334.8 | 231.0–441.3 | 1.00 / 1.32 |
| @Code Lock | junction | 1646187754 | `ESTIMATED_OFFLINE` | 1 | 83 | 6 | 5 | 0 | 43 | 54.0 | 36.6–72.4 | 0.16 / 0.22 |
| @Cold War Uniforms- Project Iron | junction | 1999666859 | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @Collectable Items | junction | 2575183443 | `ESTIMATED_OFFLINE` | 2 | 3 | 0 | 1 | 0 | 3 | 2.0 | 1.1–3.1 | 0.01 / 0.01 |
| @Community-Online-Tools | junction | 1564026768 | `ESTIMATED_OFFLINE` | 4 | 274 | 52 | 853 | 4 | 218 | 178.1 | 119.5–241.2 | 0.53 / 0.72 |
| @CookZ | junction | 3302732231 | `ESTIMATED_OFFLINE` | 1 | 106 | 3 | 15 | 0 | 26 | 68.9 | 47.4–91.1 | 0.21 / 0.27 |
| @Crocodile | junction | 3013430583 | `ESTIMATED_OFFLINE` | 1 | 39 | 0 | 0 | 0 | 22 | 25.4 | 17.0–34.3 | 0.08 / 0.10 |
| @Crocos Quadbike | junction | 2757080411 | `ESTIMATED_OFFLINE` | 3 | 14 | 0 | 0 | 0 | 8 | 9.1 | 6.0–12.6 | 0.03 / 0.04 |
| @CS Base Building Assets | junction | 3225108580 | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @CS Modular Cave | junction | 3238690259 | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @Custom Keycards | junction | 2810212624 | `ESTIMATED_OFFLINE` | 2 | 59 | 34 | 3 | 0 | 26 | 38.4 | 26.0–51.4 | 0.11 / 0.15 |
| @CZ No-Fly Zones | junction | 3051379451 | `ESTIMATED_OFFLINE` | 1 | 28 | 14 | 0 | 0 | 17 | 18.2 | 12.1–24.8 | 0.05 / 0.07 |
| @CZLRoadConeSnapping | junction | 2502418353 | `ESTIMATED_OFFLINE` | 1 | 7 | 0 | 0 | 0 | 4 | 4.5 | 2.9–6.5 | 0.01 / 0.02 |
| @Dabs Framework | junction | 2545327648 | `ESTIMATED_OFFLINE` | 2 | 23 | 361 | 17 | 261 | 227 | 15.0 | 5.1–29.4 | 0.04 / 0.09 |
| @Dabs_CodexW1 | physical | — | `ESTIMATED_OFFLINE` | 2 | 23 | 361 | 17 | 261 | 227 | 15.0 | 5.1–29.4 | 0.04 / 0.09 |
| @DAG_AnimalSkinning | junction | 3371242112 | `ESTIMATED_OFFLINE` | 1 | 15 | 47 | 1 | 0 | 6 | 9.8 | 6.5–13.4 | 0.03 / 0.04 |
| @DAG_Particle | junction | 3689811578 | `ESTIMATED_OFFLINE` | 1 | 16 | 0 | 0 | 0 | 1 | 10.4 | 7.0–14.0 | 0.03 / 0.04 |
| @DayZ Editor Loader | junction | 2276010135 | `ESTIMATED_OFFLINE` | 1 | 0 | 0 | 8 | 0 | 1 | 0.0 | 0.0–0.5 | 0.00 / 0.00 |
| @DayZ Horse | junction | 3295021220 | `ESTIMATED_OFFLINE` | 1 | 169 | 3 | 12 | 0 | 62 | 109.9 | 75.3–145.8 | 0.33 / 0.43 |
| @DayZ Mining System with Ores and Gems | junction | 2794626429 | `ESTIMATED_OFFLINE` | 1 | 175 | 0 | 2 | 0 | 45 | 113.8 | 78.4–150.2 | 0.34 / 0.45 |
| @DayZ Mining System with Ores and Gems (Legacy) | junction | 2794626429 | `ESTIMATED_OFFLINE` | 1 | 175 | 0 | 2 | 0 | 45 | 113.8 | 78.4–150.2 | 0.34 / 0.45 |
| @DayZ Mining System with Ores and Gems V2 | junction | 3604049451 | `ESTIMATED_OFFLINE` | 1 | 581 | 0 | 4 | 0 | 97 | 377.7 | 261.9–495.5 | 1.13 / 1.48 |
| @DayZ-Bicycle | junction | 2971190303 | `ESTIMATED_OFFLINE` | 2 | 59 | 0 | 0 | 0 | 27 | 38.4 | 26.0–51.4 | 0.11 / 0.15 |
| @DayZ-Expansion | junction | 2116151222 | `ESTIMATED_OFFLINE` | 32 | 522 | 41 | 83 | 1 | 106 | 339.3 | 234.9–446.0 | 1.01 / 1.33 |
| @DayZ-Expansion-AI | junction | 2792982069 | `ESTIMATED_OFFLINE` | 9 | 1152 | 97 | 103 | 4 | 263 | 748.8 | 517.9–985.0 | 2.23 / 2.94 |
| @DayZ-Expansion-Animations | junction | 2793893086 | `ESTIMATED_OFFLINE` | 4 | 0 | 0 | 0 | 0 | 1 | 0.0 | 0.0–0.5 | 0.00 / 0.00 |
| @DayZ-Expansion-Book | junction | 2572324799 | `ESTIMATED_OFFLINE` | 5 | 15 | 29 | 204 | 1 | 57 | 9.8 | 5.3–15.5 | 0.03 / 0.05 |
| @DayZ-Expansion-Bundle | junction | 2572331007 | `ESTIMATED_OFFLINE` | 245 | 5869 | 1598 | 1790 | 44 | 1796 | 3814.8 | 2629.3–5035.6 | 11.37 / 15.01 |
| @DayZ-Expansion-Chat | junction | 2792982897 | `ESTIMATED_OFFLINE` | 4 | 7 | 11 | 42 | 0 | 13 | 4.5 | 2.7–6.9 | 0.01 / 0.02 |
| @DayZ-Expansion-Core | junction | 2291785308 | `ESTIMATED_OFFLINE` | 29 | 846 | 597 | 125 | 28 | 377 | 549.9 | 376.1–731.2 | 1.64 / 2.18 |
| @DayZ-Expansion-Hardline | junction | 2828487396 | `ESTIMATED_OFFLINE` | 5 | 33 | 123 | 28 | 0 | 28 | 21.4 | 14.2–29.5 | 0.06 / 0.09 |
| @DayZ-Expansion-Licensed | junction | 2116157322 | `SCAN_OK_NO_C_SOURCE` | 35 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @DayZ-Expansion-Map-Assets | junction | 2792983824 | `ESTIMATED_OFFLINE` | 14 | 23 | 0 | 0 | 0 | 20 | 15.0 | 9.8–20.7 | 0.04 / 0.06 |
| @DayZ-Expansion-Market | junction | 2572328470 | `ESTIMATED_OFFLINE` | 10 | 424 | 326 | 492 | 1 | 205 | 275.6 | 188.0–367.4 | 0.82 / 1.09 |
| @DayZ-Expansion-Missions | junction | 2792984177 | `ESTIMATED_OFFLINE` | 5 | 189 | 90 | 0 | 1 | 35 | 122.9 | 85.0–161.6 | 0.37 / 0.48 |
| @DayZ-Expansion-Name-Tags | junction | 2576460232 | `ESTIMATED_OFFLINE` | 3 | 0 | 11 | 16 | 0 | 5 | 0.0 | 0.0–0.6 | 0.00 / 0.00 |
| @DayZ-Expansion-Navigation | junction | 2792984722 | `ESTIMATED_OFFLINE` | 6 | 59 | 30 | 141 | 1 | 28 | 38.4 | 26.0–51.5 | 0.11 / 0.15 |
| @DayZ-Expansion-Personal-Storage | junction | 2946236937 | `ESTIMATED_OFFLINE` | 7 | 99 | 48 | 95 | 0 | 35 | 64.4 | 44.0–85.6 | 0.19 / 0.25 |
| @DayZ-Expansion-Quests | junction | 2828486817 | `ESTIMATED_OFFLINE` | 8 | 721 | 14 | 80 | 1 | 110 | 468.7 | 325.3–614.3 | 1.40 / 1.83 |
| @DayZ-Expansion-RepRequirement | junction | 3422481667 | `ESTIMATED_OFFLINE` | 1 | 0 | 0 | 4 | 0 | 1 | 0.0 | 0.0–0.5 | 0.00 / 0.00 |
| @DayZ-Expansion-Spawn-Selection | junction | 2804241648 | `ESTIMATED_OFFLINE` | 4 | 1 | 61 | 67 | 1 | 18 | 0.7 | 0.0–2.0 | 0.00 / 0.01 |
| @DayZ-Expansion-Vehicles | junction | 2291785437 | `ESTIMATED_OFFLINE` | 32 | 913 | 52 | 114 | 2 | 213 | 593.5 | 410.3–780.9 | 1.77 / 2.33 |
| @DayZ-Foras-Core | junction | 3423530706 | `ESTIMATED_OFFLINE` | 1 | 0 | 0 | 5 | 0 | 1 | 0.0 | 0.0–0.5 | 0.00 / 0.00 |
| @DayZ-Foras-Vehicles | junction | 3490814209 | `ESTIMATED_OFFLINE` | 124 | 449 | 4 | 0 | 1048 | 352 | 291.9 | 196.1–394.7 | 0.87 / 1.18 |
| @DayZ-Rabbit | junction | 2948562704 | `ESTIMATED_OFFLINE` | 1 | 2 | 0 | 0 | 0 | 1 | 1.3 | 0.7–2.2 | 0.00 / 0.01 |
| @DayZ_MCP | physical | — | `ESTIMATED_OFFLINE` | 1 | 15 | 0 | 107 | 0 | 8 | 9.8 | 6.4–13.4 | 0.03 / 0.04 |
| @DayZCasinoV2 | junction | 1940425039 | `ESTIMATED_OFFLINE` | 1 | 95 | 3 | 6 | 0 | 43 | 61.8 | 42.0–82.5 | 0.18 / 0.25 |
| @DayzUnderground | junction | 1654462998 | `ESTIMATED_OFFLINE` | 25 | 1431 | 30 | 112 | 55 | 720 | 930.1 | 634.5–1240.0 | 2.77 / 3.70 |
| @dbo_surfaces_DI | junction | 2302717234 | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @Deadfall | junction | 3050117454 | `ESTIMATED_OFFLINE` | 42 | 105 | 7 | 45 | 0 | 51 | 68.2 | 46.4–91.3 | 0.20 / 0.27 |
| @Deadline | junction | 3538149928 | `ESTIMATED_OFFLINE` | 24 | 4161 | 1073 | 545 | 23 | 1276 | 2704.7 | 1864.0–3570.4 | 8.06 / 10.64 |
| @DeerIsle | junction | 1602372402 | `ESTIMATED_OFFLINE` | 64 | 358 | 41 | 58 | 3 | 157 | 232.7 | 159.1–309.6 | 0.69 / 0.92 |
| @DeerIsle Official (Experimental - Dev Build) | junction | 1750506510 | `ESTIMATED_OFFLINE` | 73 | 1499 | 60 | 127 | 3 | 314 | 974.4 | 674.7–1280.3 | 2.90 / 3.82 |
| @DF-Test | junction | 2547360544 | `ESTIMATED_OFFLINE` | 2 | 23 | 362 | 17 | 261 | 227 | 15.0 | 5.1–29.4 | 0.04 / 0.09 |
| @DNA Keycards | junction | 2714183642 | `ESTIMATED_OFFLINE` | 8 | 267 | 177 | 24 | 2 | 869 | 173.6 | 101.5–262.8 | 0.52 / 0.78 |
| @DobermannPack | junction | 3147280706 | `ESTIMATED_OFFLINE` | 72 | 1600 | 461 | 370 | 5901 | 98142 | 1040.0 | 0.0–5498.9 | 3.10 / 16.39 |
| @DobermannPackClothing | junction | 3494089216 | `SCAN_OK_NO_C_SOURCE` | 25 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @DobermannPackItems | junction | 3494092051 | `ESTIMATED_OFFLINE` | 51 | 583 | 294 | 137 | 7 | 445 | 378.9 | 254.9–511.9 | 1.13 / 1.53 |
| @DobermannPackWeapons | junction | 3534331948 | `ESTIMATED_OFFLINE` | 24 | 604 | 31 | 5 | 2 | 358 | 392.6 | 266.4–525.9 | 1.17 / 1.57 |
| @DobermannVehicles | junction | 3146928335 | `ESTIMATED_OFFLINE` | 60 | 835 | 0 | 7 | 886 | 3469 | 542.8 | 300.8–852.6 | 1.62 / 2.54 |
| @Dogtags | junction | 2303554682 | `ESTIMATED_OFFLINE` | 7 | 15 | 4 | 3 | 0 | 14 | 9.8 | 6.3–13.7 | 0.03 / 0.04 |
| @downbad serverpack main | junction | 3326613966 | `ESTIMATED_OFFLINE` | 168 | 4417 | 1073 | 1065 | 1124 | 49826 | 2871.1 | 876.0–5837.9 | 8.56 / 17.40 |
| @DrugsPLUS | junction | 2170927235 | `ESTIMATED_OFFLINE` | 1 | 55 | 20 | 4 | 0 | 32 | 35.8 | 24.1–48.2 | 0.11 / 0.14 |
| @DudeWheresMyCar | junction | 3491661516 | `ESTIMATED_OFFLINE` | 1 | 24 | 2 | 1 | 0 | 22 | 15.6 | 10.2–21.6 | 0.05 / 0.06 |
| @Dutch Army Clothing | junction | 3097629884 | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @Ear-Plugs | junction | 1819514788 | `ESTIMATED_OFFLINE` | 1 | 5 | 0 | 4 | 0 | 4 | 3.2 | 2.0–4.8 | 0.01 / 0.01 |
| @Early Winter or late Fall in Chernarus | junction | 3303988563 | `ESTIMATED_OFFLINE` | 15 | 26 | 0 | 0 | 0 | 1 | 16.9 | 11.6–22.4 | 0.05 / 0.07 |
| @Early Winter or Late Fall Livonia | junction | 3346291656 | `ESTIMATED_OFFLINE` | 15 | 26 | 0 | 0 | 0 | 1 | 16.9 | 11.6–22.4 | 0.05 / 0.07 |
| @Event arenas (mod) | junction | 3129435301 | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @ExpandedBuilding | physical | — | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @Fast Travel | junction | 1843000706 | `ESTIMATED_OFFLINE` | 1 | 32 | 0 | 0 | 0 | 13 | 20.8 | 14.0–28.0 | 0.06 / 0.08 |
| @FC_Core | junction | 3417463523 | `ESTIMATED_OFFLINE` | 1 | 49 | 0 | 0 | 0 | 26176 | 31.9 | 0.0–1147.8 | 0.09 / 3.42 |
| @FC_Fish_Equip | junction | 2937138060 | `ESTIMATED_OFFLINE` | 1 | 341 | 34 | 0 | 0 | 47470 | 221.7 | 0.0–2294.2 | 0.66 / 6.84 |
| @FC_Uaz_Pickup_Rest | junction | 3534186266 | `ESTIMATED_OFFLINE` | 1 | 51 | 0 | 0 | 0 | 10786 | 33.1 | 0.0–499.2 | 0.10 / 1.49 |
| @Fishy Buildings | junction | 2572501944 | `ESTIMATED_OFFLINE` | 28 | 1 | 0 | 0 | 0 | 11 | 0.7 | 0.0–1.7 | 0.00 / 0.01 |
| @FlipTransport | junction | 1832448183 | `ESTIMATED_OFFLINE` | 4 | 30 | 5 | 13 | 0 | 31 | 19.5 | 12.7–27.1 | 0.06 / 0.08 |
| @Forward Operator Gear | junction | 2931560672 | `ESTIMATED_OFFLINE` | 8 | 94 | 0 | 0 | 0 | 44 | 61.1 | 41.5–81.7 | 0.18 / 0.24 |
| @FrontBases | junction | 3596257559 | `SCAN_OK_NO_C_SOURCE` | 3 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @FrontBox | junction | 3649304502 | `ESTIMATED_OFFLINE` | 14 | 220 | 676 | 487 | 5 | 1238 | 143.0 | 71.7–238.6 | 0.43 / 0.71 |
| @FrontCars | junction | 3749737413 | `ESTIMATED_OFFLINE` | 33 | 1220 | 21 | 3 | 221 | 191607 | 793.0 | 0.0–9126.7 | 2.36 / 27.20 |
| @FrontChernarus | junction | 3746073297 | `UNKNOWN_TIMEOUT` | 145 | — | — | — | — | — | — | — | — |
| @FrontCherno2035 | junction | 3639075631 | `ESTIMATED_OFFLINE` | 20 | 532 | 913 | 823 | 807 | 50584 | 345.8 | 0.0–2587.1 | 1.03 / 7.71 |
| @Frontera Barrels | junction | 3471538853 | `ESTIMATED_OFFLINE` | 1 | 3 | 0 | 0 | 0 | 3 | 2.0 | 1.1–3.1 | 0.01 / 0.01 |
| @Frontera Cars | junction | 3548104050 | `ESTIMATED_OFFLINE` | 176 | 4988 | 52 | 1871 | 2382 | 81543 | 3242.2 | 414.2–7660.5 | 9.66 / 22.83 |
| @FronteraThermals | junction | 3395003618 | `ESTIMATED_OFFLINE` | 3 | 38 | 2 | 0 | 0 | 3 | 24.7 | 17.0–32.7 | 0.07 / 0.10 |
| @FrontIsle | junction | 3613903873 | `ESTIMATED_OFFLINE` | 134 | 4175 | 1356 | 1453 | 2204 | 69923 | 2713.8 | 308.6–6482.5 | 8.09 / 19.32 |
| @FrontIsle2 | junction | 3630678918 | `ESTIMATED_OFFLINE` | 3 | 50 | 0 | 2 | 901 | 3303 | 32.5 | 0.0–182.2 | 0.10 / 0.54 |
| @FrontIsleCars | junction | 3614228145 | `UNKNOWN_TIMEOUT` | 27 | — | — | — | — | — | — | — | — |
| @FrontIsleCars2 | junction | 3620515124 | `ESTIMATED_OFFLINE` | 7 | 36 | 0 | 0 | 105 | 37 | 23.4 | 15.3–32.4 | 0.07 / 0.10 |
| @FrontMusic | junction | 3553534257 | `SCAN_OK_NO_C_SOURCE` | 5 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @FrontMusicTest | junction | 3613568531 | `SCAN_OK_NO_C_SOURCE` | 5 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @FrontNaves | junction | 3550729869 | `ESTIMATED_OFFLINE` | 4 | 28 | 0 | 2 | 0 | 3773 | 18.2 | 0.0–183.5 | 0.05 / 0.55 |
| @FrontPack | junction | 3521467428 | `UNKNOWN_TIMEOUT` | 114 | — | — | — | — | — | — | — | — |
| @FrontPack2.0 | junction | 3663745754 | `ESTIMATED_OFFLINE` | 113 | 4733 | 1447 | 1326 | 2600 | 175754 | 3076.5 | 0.0–11425.4 | 9.17 / 34.05 |
| @FS | junction | 2428595209 | `ESTIMATED_OFFLINE` | 8 | 3 | 0 | 0 | 0 | 5 | 2.0 | 1.0–3.2 | 0.01 / 0.01 |
| @FS_English_Translation | junction | 2195930908 | `SCAN_OK_NO_C_SOURCE` | 6 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @GameLabs | junction | 2464526692 | `ESTIMATED_OFFLINE` | 3 | 138 | 111 | 147 | 0 | 108 | 89.7 | 60.1–121.6 | 0.27 / 0.36 |
| @GardeningPlus | junction | 3690290409 | `ESTIMATED_OFFLINE` | 1 | 1 | 5 | 6 | 0 | 5 | 0.7 | 0.1–1.5 | 0.00 / 0.00 |
| @GC Medicine Injector | junction | 3581166104 | `ESTIMATED_OFFLINE` | 1 | 0 | 0 | 0 | 12 | 223 | 0.0 | 0.0–9.8 | 0.00 / 0.03 |
| @gebsfish | junction | 2757509117 | `ESTIMATED_OFFLINE` | 1 | 284 | 136 | 18 | 4 | 90 | 184.6 | 126.9–244.2 | 0.55 / 0.73 |
| @GSC Gameworld Assets (JMC Edition) | junction | 3154500253 | `ESTIMATED_OFFLINE` | 8 | 13 | 0 | 0 | 0 | 7 | 8.5 | 5.5–11.7 | 0.03 / 0.03 |
| @GSC Gameworld Assets 2026 | junction | 3711012403 | `ESTIMATED_OFFLINE` | 8 | 13 | 0 | 0 | 0 | 7 | 8.5 | 5.5–11.7 | 0.03 / 0.03 |
| @GunnerTruckOshkosh | junction | 2512575701 | `ESTIMATED_OFFLINE` | 1 | 53 | 0 | 0 | 0 | 8 | 34.5 | 23.7–45.5 | 0.10 / 0.14 |
| @Haralds Armory | junction | 3325200572 | `ESTIMATED_OFFLINE` | 5 | 234 | 0 | 0 | 0 | 173 | 152.1 | 102.3–205.5 | 0.45 / 0.61 |
| @HealthBar-Ninjins | junction | 3487506464 | `ESTIMATED_OFFLINE` | 1 | 37 | 11 | 3 | 0 | 15 | 24.1 | 16.3–32.3 | 0.07 / 0.10 |
| @Hollow Test | junction | 3695820675 | `ESTIMATED_OFFLINE` | 54 | 1257 | 956 | 781 | 1957 | 42121 | 817.1 | 0.0–2842.2 | 2.44 / 8.47 |
| @how to add custom sounds and music to any object | junction | 2892025495 | `ESTIMATED_OFFLINE` | 1 | 0 | 0 | 0 | 0 | 1 | 0.0 | 0.0–0.5 | 0.00 / 0.00 |
| @IAT_Crafting_Plus | junction | 3142819902 | `ESTIMATED_OFFLINE` | 3 | 23 | 15 | 1 | 0 | 11 | 15.0 | 10.0–20.3 | 0.04 / 0.06 |
| @iM7s_DrugRunning | junction | 3465841846 | `ESTIMATED_OFFLINE` | 1 | 868 | 39 | 5 | 0 | 133 | 564.2 | 391.7–739.5 | 1.68 / 2.20 |
| @Immersive Placing | junction | 2521460498 | `ESTIMATED_OFFLINE` | 1 | 0 | 0 | 0 | 14 | 5 | 0.0 | 0.0–0.6 | 0.00 / 0.00 |
| @InediaInfectedAI | junction | 3031784065 | `ESTIMATED_OFFLINE` | 1 | 2286 | 342 | 124 | 0 | 107 | 1485.9 | 1037.5–1936.6 | 4.43 / 5.77 |
| @Inventory Move Sounds | junction | 2444247391 | `ESTIMATED_OFFLINE` | 2 | 2 | 0 | 0 | 5 | 8 | 1.3 | 0.5–2.5 | 0.00 / 0.01 |
| @InventoryPlusPlus | junction | 1663971788 | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @JD's Animated Weapons | junction | 3297234994 | `ESTIMATED_OFFLINE` | 6 | 55 | 5 | 0 | 0 | 18 | 35.8 | 24.4–47.7 | 0.11 / 0.14 |
| @KarmaKrew | junction | 2864245850 | `ESTIMATED_OFFLINE` | 63 | 954 | 702 | 507 | 387 | 1074 | 620.1 | 409.4–851.9 | 1.85 / 2.54 |
| @KarmaKrew Chernarus Balance | junction | 3033180005 | `ESTIMATED_OFFLINE` | 9 | 12 | 63 | 56 | 0 | 35 | 7.8 | 4.4–12.0 | 0.02 / 0.04 |
| @KeyCard-Rooms | junction | 2620165863 | `ESTIMATED_OFFLINE` | 5 | 46 | 0 | 0 | 0 | 16 | 29.9 | 20.3–40.0 | 0.09 / 0.12 |
| @KillAssets | junction | 2941533750 | `SCAN_OK_NO_C_SOURCE` | 29 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @KT_Ariel_Atom_V8_Free Version | junction | 3601793858 | `ESTIMATED_OFFLINE` | 3 | 1 | 0 | 0 | 87 | 1454 | 0.7 | 0.0–62.7 | 0.00 / 0.19 |
| @kt_roadkill_armed | physical | — | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @kt_roadkill_scum | physical | — | `ESTIMATED_OFFLINE` | 2 | 1 | 0 | 0 | 54 | 981 | 0.7 | 0.0–42.7 | 0.00 / 0.13 |
| @La Frontera Cars | junction | 3485438937 | `UNKNOWN_TIMEOUT` | 170 | — | — | — | — | — | — | — | — |
| @LaFronteraAssets | junction | 3492141189 | `ESTIMATED_OFFLINE` | 2 | 14 | 14 | 14 | 204 | 3978 | 9.1 | 0.0–180.3 | 0.03 / 0.54 |
| @LaFronteraCherno | junction | 3489672406 | `ESTIMATED_OFFLINE` | 108 | 4574 | 1155 | 1214 | 2857 | 167195 | 2973.1 | 0.0–10929.4 | 8.86 / 32.57 |
| @LaFronteraDI-Cars | junction | 3553230641 | `ESTIMATED_OFFLINE` | 60 | 743 | 1 | 0 | 312 | 232 | 482.9 | 332.6–638.1 | 1.44 / 1.90 |
| @LaFronteraMM-Cars | junction | 3553230641 | `ESTIMATED_OFFLINE` | 60 | 743 | 1 | 0 | 312 | 232 | 482.9 | 332.6–638.1 | 1.44 / 1.90 |
| @LaFronteraMM-CarsB | junction | 3600100048 | `UNKNOWN_TIMEOUT` | 176 | — | — | — | — | — | — | — | — |
| @LaFronteraValning.S.P | junction | 3555948477 | `UNKNOWN_TIMEOUT` | 62 | — | — | — | — | — | — | — | — |
| @LaFronteraValning.S.Pv2 | junction | 3567752439 | `ESTIMATED_OFFLINE` | 9 | 49 | 10 | 1 | 0 | 29 | 31.9 | 21.4–43.1 | 0.09 / 0.13 |
| @LB_Admin | physical | — | `ESTIMATED_OFFLINE` | 3 | 293 | 813 | 708 | 5 | 509 | 190.5 | 121.5–269.5 | 0.57 / 0.80 |
| @LB_ServerSide | physical | — | `ESTIMATED_OFFLINE` | 2 | 18 | 48 | 19 | 1733 | 2032 | 11.7 | 0.0–101.5 | 0.03 / 0.30 |
| @LBAdmin TerjeSkill addon | junction | 3636241428 | `ESTIMATED_OFFLINE` | 1 | 7 | 1 | 14 | 0 | 5 | 4.5 | 2.8–6.5 | 0.01 / 0.02 |
| @LBGroups GPS Navigation | junction | 3717358466 | `ESTIMATED_OFFLINE` | 2 | 84 | 4 | 25 | 0 | 20 | 54.6 | 37.5–72.2 | 0.16 / 0.22 |
| @LBmaster_Core | physical | — | `ESTIMATED_OFFLINE` | 1 | 81 | 503 | 88 | 0 | 216 | 52.6 | 31.7–78.0 | 0.16 / 0.23 |
| @LBmaster_Groups | physical | — | `ESTIMATED_OFFLINE` | 1 | 142 | 169 | 273 | 2 | 148 | 92.3 | 61.0–126.7 | 0.28 / 0.38 |
| @LF1.1 | junction | 3353992922 | `ESTIMATED_OFFLINE` | 104 | 1805 | 1242 | 1352 | 825 | 20627 | 1173.2 | 351.8–2397.1 | 3.50 / 7.14 |
| @LF_NoBuild | physical | — | `ESTIMATED_OFFLINE` | 1 | 3 | 0 | 0 | 0 | 1 | 2.0 | 1.1–3.0 | 0.01 / 0.01 |
| @LF_VStorage | physical | — | `ESTIMATED_OFFLINE` | 1 | 1213 | 80 | 9 | 0 | 63 | 788.5 | 550.3–1028.1 | 2.35 / 3.06 |
| @LFCarlock | junction | 3558973301 | `ESTIMATED_OFFLINE` | 1 | 32 | 10 | 1 | 0 | 24 | 20.8 | 13.8–28.5 | 0.06 / 0.08 |
| @LFGungame | physical | — | `ESTIMATED_OFFLINE` | 1 | 126 | 27 | 15 | 0 | 15 | 81.9 | 56.8–107.5 | 0.24 / 0.32 |
| @LFHeli | physical | — | `ESTIMATED_OFFLINE` | 1 | 129 | 16 | 0 | 0 | 10 | 83.9 | 58.2–109.8 | 0.25 / 0.33 |
| @LFHeli_HH60G | physical | — | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @LFHeli_OH1 | physical | — | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @LFHeliCore | physical | — | `ESTIMATED_OFFLINE` | 1 | 187 | 16 | 0 | 0 | 11 | 121.5 | 84.6–158.9 | 0.36 / 0.47 |
| @LFInfectedBig | physical | — | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @LFPowerGrid | physical | — | `ESTIMATED_OFFLINE` | 1 | 2246 | 199 | 21 | 0 | 144 | 1459.9 | 1018.4–1904.4 | 4.35 / 5.68 |
| @LFPowerGrid_A1 | physical | — | `ESTIMATED_OFFLINE` | 1 | 2244 | 198 | 21 | 0 | 128 | 1458.6 | 1017.9–1902.0 | 4.35 / 5.67 |
| @LFPowerGrid_A2 | physical | — | `ESTIMATED_OFFLINE` | 1 | 2246 | 199 | 21 | 0 | 144 | 1459.9 | 1018.4–1904.4 | 4.35 / 5.68 |
| @LFQuad | physical | — | `ESTIMATED_OFFLINE` | 1 | 12 | 0 | 0 | 0 | 5 | 7.8 | 5.1–10.8 | 0.02 / 0.03 |
| @LFQuad2 | physical | — | `ESTIMATED_OFFLINE` | 1 | 1 | 0 | 0 | 0 | 1 | 0.7 | 0.2–1.3 | 0.00 / 0.00 |
| @LFQuad3 | physical | — | `UNKNOWN_UNREADABLE_INDEX` | 1 | — | — | — | — | — | — | — | — |
| @LFSlidingFloor | physical | — | `ESTIMATED_OFFLINE` | 1 | 22 | 0 | 3 | 0 | 7 | 14.3 | 9.6–19.3 | 0.04 / 0.06 |
| @LFTEST | junction | 3560471372 | `ESTIMATED_OFFLINE` | 37 | 4153 | 1214 | 1038 | 62 | 2144 | 2699.5 | 1840.6–3600.3 | 8.05 / 10.73 |
| @LIGHTS | junction | 2822125184 | `ESTIMATED_OFFLINE` | 2 | 27 | 0 | 0 | 0 | 12 | 17.6 | 11.8–23.7 | 0.05 / 0.07 |
| @Loot Barrel Transfer | junction | 2832887415 | `ESTIMATED_OFFLINE` | 1 | 1 | 0 | 0 | 0 | 1 | 0.7 | 0.2–1.3 | 0.00 / 0.00 |
| @Lost Paradise Assets | junction | 3588485220 | `ESTIMATED_OFFLINE` | 38 | 524 | 89 | 40 | 193 | 3493 | 340.6 | 158.7–590.8 | 1.02 / 1.76 |
| @Lost Paradise SP1 | physical | — | `SCAN_OK_NO_C_SOURCE` | 0 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @Lost Paradise SP2 | physical | — | `SCAN_OK_NO_C_SOURCE` | 0 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @Lost Paradise SP3 | physical | — | `SCAN_OK_NO_C_SOURCE` | 0 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @Lost Paradise SP4 | physical | — | `SCAN_OK_NO_C_SOURCE` | 0 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @Lost Paradise ZA Terje Pack | junction | 3648079942 | `ESTIMATED_OFFLINE` | 8 | 1117 | 134 | 186 | 12 | 654 | 726.1 | 493.1–971.9 | 2.16 / 2.90 |
| @LostParadiseVehicleTest | physical | — | `SCAN_OK_NO_C_SOURCE` | 0 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @MadEagle Tools n Melee | junction | 3106399005 | `ESTIMATED_OFFLINE` | 1 | 0 | 0 | 0 | 0 | 1 | 0.0 | 0.0–0.5 | 0.00 / 0.00 |
| @Mag Obfuscation | junction | 2449234595 | `ESTIMATED_OFFLINE` | 1 | 1 | 0 | 6 | 5 | 10 | 0.7 | 0.0–1.7 | 0.00 / 0.01 |
| @MagLoaderBox | junction | 3164839000 | `ESTIMATED_OFFLINE` | 1 | 4 | 1 | 0 | 173 | 3244 | 2.6 | 0.0–140.9 | 0.01 / 0.42 |
| @MaharlikaPH_Boats | junction | 3354681846 | `ESTIMATED_OFFLINE` | 11 | 256 | 16 | 5 | 0 | 119 | 166.4 | 113.5–221.8 | 0.50 / 0.66 |
| @MapLink Hive | junction | 3478983598 | `ESTIMATED_OFFLINE` | 9 | 188 | 190 | 33 | 6 | 124 | 122.2 | 82.5–164.5 | 0.36 / 0.49 |
| @Mass'sManyItemOverhaul | junction | 1566911166 | `ESTIMATED_OFFLINE` | 6 | 527 | 5 | 3 | 0 | 159 | 342.6 | 235.9–452.5 | 1.02 / 1.35 |
| @MBM_DeerIsleBridge | junction | 3331306860 | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @MBM_UnderglowLights | junction | 3307457670 | `ESTIMATED_OFFLINE` | 1 | 25 | 0 | 0 | 0 | 6 | 16.2 | 11.0–21.8 | 0.05 / 0.06 |
| @MCPTest | physical | — | `ESTIMATED_OFFLINE` | 1 | 0 | 0 | 3 | 0 | 1 | 0.0 | 0.0–0.5 | 0.00 / 0.00 |
| @MERCEDES_AMGLF | physical | — | `ESTIMATED_OFFLINE` | 1 | 7 | 0 | 0 | 0 | 3 | 4.5 | 2.9–6.5 | 0.01 / 0.02 |
| @Meru Car Pack English Version | junction | 3511486256 | `ESTIMATED_OFFLINE` | 42 | 689 | 0 | 0 | 0 | 255 | 447.9 | 307.5–593.4 | 1.33 / 1.77 |
| @MilitaryBunker | physical | — | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @MMG - Mightys Military Gear | junction | 2663169692 | `ESTIMATED_OFFLINE` | 1 | 30 | 0 | 0 | 0 | 24 | 19.5 | 12.9–26.8 | 0.06 / 0.08 |
| @MMG Base Storage | junction | 3210162677 | `ESTIMATED_OFFLINE` | 1 | 357 | 1 | 0 | 0 | 148 | 232.1 | 158.8–308.3 | 0.69 / 0.92 |
| @Modern Lights - LF PowerGrid (Addon) | junction | 3750674225 | `ESTIMATED_OFFLINE` | 1 | 32 | 0 | 1 | 0 | 11 | 20.8 | 14.1–27.9 | 0.06 / 0.08 |
| @Money Ruble | junction | 2751395477 | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @Montepinar | junction | 3693996643 | `ESTIMATED_OFFLINE` | 68 | 1558 | 1395 | 1207 | 102 | 2574 | 1012.7 | 650.1–1425.7 | 3.02 / 4.25 |
| @MoonShining | junction | 3301234558 | `ESTIMATED_OFFLINE` | 2 | 28 | 2 | 0 | 0 | 17 | 18.2 | 12.1–24.8 | 0.05 / 0.07 |
| @MuchCarKey | junction | 2049002856 | `ESTIMATED_OFFLINE` | 2 | 77 | 5 | 1 | 0 | 46 | 50.1 | 33.8–67.4 | 0.15 / 0.20 |
| @MuchDecos | junction | 1967655509 | `ESTIMATED_OFFLINE` | 1 | 88 | 6 | 0 | 0 | 62 | 57.2 | 38.4–77.4 | 0.17 / 0.23 |
| @MuchFramework | junction | 3171576913 | `ESTIMATED_OFFLINE` | 2 | 143 | 2 | 5 | 0 | 55 | 93.0 | 63.6–123.6 | 0.28 / 0.37 |
| @MuchStuffPack | junction | 1991570984 | `ESTIMATED_OFFLINE` | 24 | 91 | 0 | 0 | 0 | 97 | 59.1 | 39.0–81.4 | 0.18 / 0.24 |
| @MuchStuffPack_Fix | junction | 3492739269 | `ESTIMATED_OFFLINE` | 1 | 0 | 0 | 0 | 1 | 21 | 0.0 | 0.0–1.3 | 0.00 / 0.00 |
| @Mystery | junction | 3152381816 | `ESTIMATED_OFFLINE` | 20 | 524 | 782 | 514 | 10 | 570 | 340.6 | 225.2–467.3 | 1.02 / 1.39 |
| @Mystery Box | junction | 2574104127 | `ESTIMATED_OFFLINE` | 2 | 25 | 0 | 0 | 5 | 22 | 16.2 | 10.6–22.5 | 0.05 / 0.07 |
| @Namalsk Island | junction | 2289456201 | `ESTIMATED_OFFLINE` | 9 | 25 | 7 | 7 | 0 | 28 | 16.2 | 10.5–22.7 | 0.05 / 0.07 |
| @Namalsk Survival | junction | 2289461232 | `ESTIMATED_OFFLINE` | 2 | 457 | 13 | 17 | 0 | 142 | 297.1 | 204.5–392.6 | 0.89 / 1.17 |
| @Nemsis Craftingpack All in One | junction | 3606014796 | `ESTIMATED_OFFLINE` | 18 | 3700 | 81 | 17 | 0 | 585 | 2405.0 | 1670.0–3151.6 | 7.17 / 9.39 |
| @NEW_DayZ Mining System with Ores and Gems V2 | junction | 3604049451 | `ESTIMATED_OFFLINE` | 1 | 581 | 0 | 4 | 0 | 97 | 377.7 | 261.9–495.5 | 1.13 / 1.48 |
| @Nexo Aventura | junction | 3753505225 | `ESTIMATED_OFFLINE` | 8 | 242 | 744 | 497 | 3 | 438 | 157.3 | 99.9–223.4 | 0.47 / 0.67 |
| @Ninjins-PvP-PvE | junction | 3381664818 | `ESTIMATED_OFFLINE` | 1 | 440 | 127 | 412 | 0 | 101 | 286.0 | 197.7–376.5 | 0.85 / 1.12 |
| @No Vehicle Damage Complete | junction | 3166815421 | `ESTIMATED_OFFLINE` | 1 | 19 | 2 | 0 | 0 | 9 | 12.3 | 8.2–16.9 | 0.04 / 0.05 |
| @NomNom Collectibles | junction | 3282237841 | `ESTIMATED_OFFLINE` | 4 | 254 | 53 | 7 | 3 | 73 | 165.1 | 113.7–218.1 | 0.49 / 0.65 |
| @Notifications | junction | 2353998362 | `ESTIMATED_OFFLINE` | 2 | 0 | 8 | 4 | 0 | 8 | 0.0 | 0.0–0.8 | 0.00 / 0.00 |
| @NovSer_1 | junction | 3566136204 | `SCAN_OK_NO_C_SOURCE` | 0 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @NovSer_44 | junction | 3566234101 | `SCAN_OK_NO_C_SOURCE` | 0 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @NUKETOWN in DYAZ | junction | 3706549280 | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @NY_SwitchLight | junction | 3639843555 | `ESTIMATED_OFFLINE` | 1 | 3 | 0 | 1 | 0 | 2 | 2.0 | 1.1–3.0 | 0.01 / 0.01 |
| @Objects_Free_Mapping | junction | 2866394785 | `ESTIMATED_OFFLINE` | 7 | 0 | 0 | 0 | 0 | 1 | 0.0 | 0.0–0.5 | 0.00 / 0.00 |
| @ObjectsSpawner | junction | 2768732361 | `ESTIMATED_OFFLINE` | 1 | 0 | 0 | 37 | 0 | 2 | 0.0 | 0.0–0.5 | 0.00 / 0.00 |
| @Onforin STB | junction | 3445058656 | `ESTIMATED_OFFLINE` | 5 | 6 | 12 | 0 | 0 | 8 | 3.9 | 2.3–5.8 | 0.01 / 0.02 |
| @P2PTrader (Player to Player Trader) | junction | 2012299152 | `ESTIMATED_OFFLINE` | 1 | 14 | 4 | 160 | 0 | 48 | 9.1 | 5.1–14.3 | 0.03 / 0.04 |
| @Pack-GMZ | junction | 3261698946 | `ESTIMATED_OFFLINE` | 24 | 632 | 1033 | 978 | 436 | 8944 | 410.8 | 83.9–912.3 | 1.22 / 2.72 |
| @Pack-GMZ-2 | junction | 3261699899 | `ESTIMATED_OFFLINE` | 37 | 1100 | 179 | 139 | 995 | 23222 | 715.0 | 0.0–1911.1 | 2.13 / 5.70 |
| @Pack-GMZ-3 | junction | 3390162607 | `ESTIMATED_OFFLINE` | 26 | 134 | 0 | 6 | 2 | 155 | 87.1 | 57.2–120.2 | 0.26 / 0.36 |
| @Pack-GMZ-4 | junction | 3395845324 | `ESTIMATED_OFFLINE` | 13 | 419 | 30 | 43 | 2 | 291 | 272.4 | 183.8–366.8 | 0.81 / 1.09 |
| @Pack-GMZ-5 | junction | 3496531777 | `ESTIMATED_OFFLINE` | 146 | 4340 | 31 | 1867 | 1934 | 2180 | 2821.0 | 1924.9–3759.8 | 8.41 / 11.21 |
| @Pack-GMZ-Vehiculos | junction | 3280928876 | `ESTIMATED_OFFLINE` | 86 | 1371 | 0 | 0 | 81 | 272 | 891.1 | 617.4–1170.4 | 2.66 / 3.49 |
| @Paragon Arsenal | junction | 3047952845 | `ESTIMATED_OFFLINE` | 3 | 44 | 0 | 0 | 0 | 72 | 28.6 | 18.2–40.6 | 0.09 / 0.12 |
| @Paragon Gear and Armor | junction | 2820370970 | `ESTIMATED_OFFLINE` | 2 | 26 | 0 | 0 | 0 | 20 | 16.9 | 11.1–23.2 | 0.05 / 0.07 |
| @Paragon Storage | junction | 3010267444 | `ESTIMATED_OFFLINE` | 2 | 757 | 8 | 7 | 0 | 323 | 492.1 | 336.9–653.7 | 1.47 / 1.95 |
| @Player Bounties | junction | 2845071886 | `ESTIMATED_OFFLINE` | 3 | 32 | 4 | 3 | 5 | 23 | 20.8 | 13.8–28.4 | 0.06 / 0.08 |
| @Pouch Only Mags | junction | 3634668583 | `ESTIMATED_OFFLINE` | 1 | 0 | 0 | 3 | 0 | 2 | 0.0 | 0.0–0.5 | 0.00 / 0.00 |
| @PowerGrid | junction | 3696475622 | `ESTIMATED_OFFLINE` | 1 | 2055 | 194 | 18 | 0 | 144 | 1335.8 | 931.5–1743.0 | 3.98 / 5.19 |
| @PripyatGamma v114 | junction | 3711024720 | `ESTIMATED_OFFLINE` | 18 | 1 | 2 | 0 | 3 | 3 | 0.7 | 0.2–1.4 | 0.00 / 0.00 |
| @ProTraction | junction | 3656437172 | `ESTIMATED_OFFLINE` | 1 | 3 | 0 | 0 | 0 | 3 | 2.0 | 1.1–3.1 | 0.01 / 0.01 |
| @PseudoGiant | junction | 2847957663 | `ESTIMATED_OFFLINE` | 2 | 13 | 0 | 0 | 0 | 3 | 8.5 | 5.6–11.5 | 0.03 / 0.03 |
| @PVEZ Reloaded | junction | 2831742849 | `ESTIMATED_OFFLINE` | 1 | 99 | 63 | 10 | 1 | 38 | 64.4 | 44.0–85.7 | 0.19 / 0.26 |
| @PvZmoD_CustomisableZombies | junction | 2051775667 | `ESTIMATED_OFFLINE` | 1 | 185 | 172 | 28 | 0 | 57 | 120.2 | 82.7–159.2 | 0.36 / 0.47 |
| @Quick Transfer | junction | 3697797064 | `ESTIMATED_OFFLINE` | 1 | 0 | 0 | 9 | 0 | 994 | 0.0 | 0.0–42.4 | 0.00 / 0.13 |
| @QuickMoveItemsByCategory | junction | 3332001143 | `ESTIMATED_OFFLINE` | 1 | 2 | 1 | 1 | 107 | 1916 | 1.3 | 0.0–83.1 | 0.00 / 0.25 |
| @RA Base Building | junction | 3173739565 | `ESTIMATED_OFFLINE` | 4 | 185 | 6 | 10 | 0 | 141 | 120.2 | 80.7–162.7 | 0.36 / 0.48 |
| @Radio | junction | 3070921938 | `ESTIMATED_OFFLINE` | 1 | 26 | 0 | 3 | 0 | 20 | 16.9 | 11.1–23.2 | 0.05 / 0.07 |
| @Radio Fungi RaG_Vehicle_Pack | junction | 3407149034 | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @Radio_Fungi_Car_Radio_Addon | junction | 3405725509 | `ESTIMATED_OFFLINE` | 1 | 37 | 0 | 0 | 0 | 11 | 24.1 | 16.4–32.2 | 0.07 / 0.10 |
| @Radio_Fungi_NO_Music | junction | 3434370604 | `ESTIMATED_OFFLINE` | 1 | 91 | 0 | 0 | 0 | 35 | 59.1 | 40.4–78.8 | 0.18 / 0.23 |
| @Radioactive Animals | junction | 2485015982 | `SCAN_OK_NO_C_SOURCE` | 3 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @RaG Virtual Storage (VSM) | junction | 3462249451 | `ESTIMATED_OFFLINE` | 1 | 14 | 1 | 0 | 0 | 20 | 9.1 | 5.7–13.1 | 0.03 / 0.04 |
| @RaG_BaseBuilding | junction | 3157695626 | `ESTIMATED_OFFLINE` | 1 | 159 | 28 | 5 | 0 | 108 | 103.4 | 69.7–139.3 | 0.31 / 0.42 |
| @RaG_BaseItems | junction | 2878980498 | `ESTIMATED_OFFLINE` | 29 | 570 | 23 | 2 | 0 | 245 | 370.5 | 253.5–492.4 | 1.10 / 1.47 |
| @RaG_BaseItems ToFu Virtual Storage  Addon | junction | 3283198847 | `ESTIMATED_OFFLINE` | 1 | 53 | 0 | 0 | 0 | 4 | 34.5 | 23.8–45.4 | 0.10 / 0.14 |
| @RaG_BeeHive | junction | 2879040969 | `ESTIMATED_OFFLINE` | 1 | 88 | 2 | 0 | 0 | 33 | 57.2 | 39.1–76.2 | 0.17 / 0.23 |
| @RaG_Cabin | junction | 2988726228 | `ESTIMATED_OFFLINE` | 1 | 94 | 8 | 1 | 0 | 49 | 61.1 | 41.4–81.9 | 0.18 / 0.24 |
| @RaG_Core | junction | 3556131153 | `ESTIMATED_OFFLINE` | 1 | 102 | 39 | 7 | 0 | 67 | 66.3 | 44.7–89.4 | 0.20 / 0.27 |
| @RaG_Dragons | junction | 3310715247 | `ESTIMATED_OFFLINE` | 1 | 54 | 10 | 1 | 0 | 26 | 35.1 | 23.8–47.2 | 0.10 / 0.14 |
| @RaG_Hunting_Cabin | junction | 3117613872 | `ESTIMATED_OFFLINE` | 1 | 91 | 7 | 2 | 0 | 43 | 59.1 | 40.2–79.1 | 0.18 / 0.24 |
| @RaG_Liquid_Framework | physical | — | `SCAN_OK_NO_C_SOURCE` | 0 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @RaG_Wheel_Of_Fortune | junction | 3308989311 | `ESTIMATED_OFFLINE` | 1 | 19 | 2 | 1 | 0 | 6 | 12.3 | 8.3–16.7 | 0.04 / 0.05 |
| @RedFalcon Flight System Heliz | junction | 2692979668 | `ESTIMATED_OFFLINE` | 39 | 472 | 2 | 21 | 0 | 87 | 306.8 | 212.6–402.9 | 0.91 / 1.20 |
| @RedFalcon Mosquito Mk III | junction | 2808795115 | `ESTIMATED_OFFLINE` | 1 | 187 | 2 | 20 | 0 | 29 | 121.5 | 84.2–159.7 | 0.36 / 0.48 |
| @RedFalcon Watercraft | junction | 2906371600 | `ESTIMATED_OFFLINE` | 10 | 118 | 2 | 14 | 0 | 44 | 76.7 | 52.5–102.0 | 0.23 / 0.30 |
| @ReduceCarDamage | junction | 2101898287 | `ESTIMATED_OFFLINE` | 1 | 4 | 0 | 0 | 0 | 2 | 2.6 | 1.5–3.9 | 0.01 / 0.01 |
| @Repair Stations | junction | 2921256386 | `ESTIMATED_OFFLINE` | 1 | 17 | 0 | 0 | 0 | 1 | 11.1 | 7.5–14.8 | 0.03 / 0.04 |
| @Reporter | junction | 2418079817 | `ESTIMATED_OFFLINE` | 3 | 1 | 3 | 16 | 0 | 13 | 0.7 | 0.0–1.8 | 0.00 / 0.01 |
| @Role Alert | junction | 3305000832 | `ESTIMATED_OFFLINE` | 2 | 23 | 9 | 15 | 0 | 12 | 15.0 | 10.0–20.4 | 0.04 / 0.06 |
| @Rostowmap | junction | 2344585107 | `ESTIMATED_OFFLINE` | 7 | 8 | 0 | 0 | 3 | 3 | 5.2 | 3.3–7.3 | 0.02 / 0.02 |
| @RUSForma_Motorcycles | junction | 2933663272 | `ESTIMATED_OFFLINE` | 13 | 173 | 0 | 0 | 0 | 58 | 112.5 | 77.2–149.1 | 0.34 / 0.44 |
| @RUSForma_vehicles | junction | 2536888090 | `ESTIMATED_OFFLINE` | 64 | 1257 | 0 | 0 | 0 | 340 | 817.1 | 564.0–1077.0 | 2.44 / 3.21 |
| @RUSForma_vehicles_historical | junction | 3351368240 | `ESTIMATED_OFFLINE` | 15 | 277 | 0 | 0 | 0 | 77 | 180.1 | 124.1–237.7 | 0.54 / 0.71 |
| @SALVATION SERVER MOD | junction | 3421213293 | `UNKNOWN_TIMEOUT` | 167 | — | — | — | — | — | — | — | — |
| @SchanaParty | junction | 2534883520 | `ESTIMATED_OFFLINE` | 2 | 0 | 0 | 0 | 82 | 23 | 0.0 | 0.0–1.4 | 0.00 / 0.00 |
| @SearchInventory | junction | 2936585965 | `ESTIMATED_OFFLINE` | 1 | 0 | 0 | 34 | 0 | 8 | 0.0 | 0.0–0.8 | 0.00 / 0.00 |
| @Server_Information_Panel | junction | 1680019590 | `ESTIMATED_OFFLINE` | 1 | 15 | 22 | 112 | 0 | 23 | 9.8 | 6.1–14.1 | 0.03 / 0.04 |
| @ServerCoreBanov | physical | — | `SCAN_OK_NO_C_SOURCE` | 0 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @Simple Cinematics | junction | 2547108825 | `ESTIMATED_OFFLINE` | 2 | 10 | 2 | 26 | 5 | 13 | 6.5 | 4.0–9.4 | 0.02 / 0.03 |
| @Single Use Punched Cards | junction | 3390709024 | `ESTIMATED_OFFLINE` | 1 | 0 | 0 | 0 | 0 | 1 | 0.0 | 0.0–0.5 | 0.00 / 0.00 |
| @Skullzone.S.P | junction | 3579659792 | `ESTIMATED_OFFLINE` | 62 | 1598 | 1182 | 1209 | 1717 | 102248 | 1038.7 | 0.0–5670.7 | 3.10 / 16.90 |
| @Skullzone.S.P.2 | junction | 3584107916 | `ESTIMATED_OFFLINE` | 11 | 918 | 14 | 2 | 6 | 5630 | 596.7 | 289.4–1014.0 | 1.78 / 3.02 |
| @SkyZ - Skybox Overhaul | junction | 2536780687 | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @SNAFU Weapons | junction | 2443122116 | `ESTIMATED_OFFLINE` | 11 | 857 | 0 | 4 | 0 | 177 | 557.1 | 385.7–732.1 | 1.66 / 2.18 |
| @Snowy Trees for Early Winter Late Fall | junction | 3391938881 | `SCAN_OK_NO_C_SOURCE` | 7 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @SPBuilding - Nuclear reactor coolant, walls, platforms | junction | 2177232791 | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @Squeaky Hammer - Martillo de Juguete de TitiZ | junction | 3653877398 | `ESTIMATED_OFFLINE` | 1 | 8 | 0 | 0 | 0 | 9 | 5.2 | 3.2–7.6 | 0.02 / 0.02 |
| @Stargate Teleporters Complete | junction | 2933015619 | `ESTIMATED_OFFLINE` | 1 | 32 | 0 | 0 | 0 | 10 | 20.8 | 14.1–27.9 | 0.06 / 0.08 |
| @SUB_BRZ | physical | — | `ESTIMATED_OFFLINE` | 1 | 5 | 0 | 0 | 0 | 1 | 3.2 | 2.0–4.7 | 0.01 / 0.01 |
| @Sunnyvale_Chernarus | junction | 3353462076 | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @Sunnyvale_Custom | junction | 2944621222 | `ESTIMATED_OFFLINE` | 7 | 1 | 0 | 0 | 0 | 8 | 0.7 | 0.0–1.6 | 0.00 / 0.00 |
| @Sunnyvale_DeerIsle | junction | 3353467227 | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @Sunnyvale_Licensed | junction | 2931227541 | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @Sunnyvale_Original | junction | 2812005495 | `ESTIMATED_OFFLINE` | 99 | 959 | 1155 | 1112 | 495 | 9635 | 623.4 | 216.9–1217.9 | 1.86 / 3.63 |
| @Sunnyvale_Original_II | junction | 2820741376 | `ESTIMATED_OFFLINE` | 94 | 1603 | 31 | 65 | 143 | 600 | 1042.0 | 715.5–1380.3 | 3.11 / 4.11 |
| @Sunnyvale_PVE | junction | 2820761610 | `ESTIMATED_OFFLINE` | 2 | 191 | 174 | 31 | 170 | 3162 | 124.2 | 14.7–295.4 | 0.37 / 0.88 |
| @Sunnyvale_PVP | junction | 2818997819 | `ESTIMATED_OFFLINE` | 4 | 31 | 40 | 40 | 2 | 49 | 20.2 | 12.8–28.7 | 0.06 / 0.09 |
| @Sunnyvale_Temporary | junction | 3728861769 | `ESTIMATED_OFFLINE` | 3 | 28 | 13 | 9 | 0 | 11 | 18.2 | 12.3–24.5 | 0.05 / 0.07 |
| @Survivor Animations | junction | 2918418331 | `ESTIMATED_OFFLINE` | 3 | 44 | 5 | 9 | 0 | 17 | 28.6 | 19.4–38.3 | 0.09 / 0.11 |
| @SVT 40 & AVT 40 | junction | 3477543211 | `ESTIMATED_OFFLINE` | 1 | 1 | 0 | 0 | 0 | 3 | 0.7 | 0.2–1.4 | 0.00 / 0.00 |
| @Syndicate Hideout Light | junction | 3415582120 | `ESTIMATED_OFFLINE` | 2 | 226 | 35 | 4 | 0 | 56 | 146.9 | 101.3–193.8 | 0.44 / 0.58 |
| @Takistan | junction | 2153795105 | `SCAN_OK_NO_C_SOURCE` | 17 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @TakistanPlus | junction | 2563233742 | `ESTIMATED_OFFLINE` | 10 | 7 | 2 | 0 | 8 | 17 | 4.5 | 2.6–7.1 | 0.01 / 0.02 |
| @Tent Actions Fix | junction | 3765278986 | `ESTIMATED_OFFLINE` | 1 | 5 | 0 | 0 | 0 | 4 | 3.2 | 2.0–4.8 | 0.01 / 0.01 |
| @Terje-Core | junction | 3649957186 | `ESTIMATED_OFFLINE` | 1 | 275 | 112 | 38 | 0 | 142 | 178.8 | 121.7–238.8 | 0.53 / 0.71 |
| @Terje-Skills | junction | 3649958397 | `ESTIMATED_OFFLINE` | 1 | 166 | 1 | 24 | 0 | 60 | 107.9 | 73.9–143.2 | 0.32 / 0.43 |
| @Terje-StartScreen | junction | 3649959402 | `ESTIMATED_OFFLINE` | 1 | 116 | 2 | 73 | 3 | 55 | 75.4 | 51.3–100.8 | 0.22 / 0.30 |
| @TerrainIslands | junction | 2393499239 | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @The Wall Clothing | junction | 1671313616 | `ESTIMATED_OFFLINE` | 4 | 80 | 0 | 0 | 0 | 40 | 52.0 | 35.3–69.7 | 0.15 / 0.21 |
| @The Wall Core | junction | 2709567672 | `ESTIMATED_OFFLINE` | 25 | 744 | 564 | 164 | 4 | 373 | 483.6 | 329.8–644.9 | 1.44 / 1.92 |
| @The Wall Music | junction | 1828886068 | `ESTIMATED_OFFLINE` | 4 | 104 | 96 | 51 | 0 | 79 | 67.6 | 45.3–91.6 | 0.20 / 0.27 |
| @The Wall PVEPVP Chernarus Server Mod | junction | 2876132694 | `ESTIMATED_OFFLINE` | 2 | 3 | 8 | 25 | 0 | 11 | 2.0 | 0.9–3.4 | 0.01 / 0.01 |
| @The Wall Retextures | junction | 1880658874 | `ESTIMATED_OFFLINE` | 4 | 41 | 0 | 0 | 0 | 33 | 26.7 | 17.7–36.5 | 0.08 / 0.11 |
| @The Wall Vehicles | junction | 3442814590 | `ESTIMATED_OFFLINE` | 61 | 1167 | 1 | 0 | 344 | 261 | 758.6 | 524.8–997.6 | 2.26 / 2.97 |
| @TierraDeNadie-Vehicles | junction | 3161975509 | `ESTIMATED_OFFLINE` | 67 | 536 | 1 | 0 | 674 | 280 | 348.4 | 237.3–465.2 | 1.04 / 1.39 |
| @TierraDeNadie_CORE | junction | 3070506442 | `ESTIMATED_OFFLINE` | 39 | 981 | 1242 | 1082 | 6112 | 9982 | 637.6 | 219.0–1251.1 | 1.90 / 3.73 |
| @TierraDeNadieSP | junction | 3191894994 | `UNKNOWN_TIMEOUT` | 81 | — | — | — | — | — | — | — | — |
| @TimedCrate | junction | 3247633425 | `ESTIMATED_OFFLINE` | 1 | 46 | 0 | 1 | 0 | 8 | 29.9 | 20.5–39.6 | 0.09 / 0.12 |
| @ToFu Virtual Storage | junction | 2810820431 | `ESTIMATED_OFFLINE` | 2 | 61 | 5 | 6 | 0 | 13 | 39.6 | 27.2–52.5 | 0.12 / 0.16 |
| @Tombstone Advanced Groups | junction | 2458056027 | `ESTIMATED_OFFLINE` | 1 | 5 | 0 | 1 | 0 | 5 | 3.2 | 1.9–4.9 | 0.01 / 0.01 |
| @Tombstone V++ Map | junction | 2395118966 | `ESTIMATED_OFFLINE` | 1 | 5 | 2 | 0 | 0 | 4 | 3.2 | 2.0–4.8 | 0.01 / 0.01 |
| @Towing Service | junction | 3004707934 | `ESTIMATED_OFFLINE` | 3 | 78 | 3 | 0 | 0 | 22 | 50.7 | 34.8–67.3 | 0.15 / 0.20 |
| @Tradepost Tower | junction | 3568826750 | `ESTIMATED_OFFLINE` | 1 | 0 | 0 | 0 | 0 | 1 | 0.0 | 0.0–0.5 | 0.00 / 0.00 |
| @Trader | junction | 1590841260 | `ESTIMATED_OFFLINE` | 1 | 123 | 5 | 43 | 0 | 62 | 80.0 | 54.3–107.0 | 0.24 / 0.32 |
| @Trader Deerisle  avec éclairage auto de nuit (with night lighting ) | junction | 3246506625 | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @TraderPlus | junction | 2458896948 | `ESTIMATED_OFFLINE` | 9 | 446 | 58 | 16 | 0 | 140 | 289.9 | 199.5–383.2 | 0.86 / 1.14 |
| @TraderPlus Boatlock | junction | 3555157300 | `ESTIMATED_OFFLINE` | 1 | 32 | 3 | 3 | 0 | 1060 | 20.8 | 0.0–72.2 | 0.06 / 0.22 |
| @Treasure | junction | 1982919196 | `ESTIMATED_OFFLINE` | 1 | 52 | 57 | 12 | 0 | 25 | 33.8 | 22.9–45.4 | 0.10 / 0.14 |
| @TreeHouse | junction | 3467458480 | `ESTIMATED_OFFLINE` | 1 | 0 | 0 | 0 | 0 | 1 | 0.0 | 0.0–0.5 | 0.00 / 0.00 |
| @Uncuepas Civilian Clothing | junction | 1762444175 | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @Underground Bases | junction | 3029439021 | `ESTIMATED_OFFLINE` | 1 | 85 | 3 | 1 | 0 | 41 | 55.2 | 37.5–74.0 | 0.16 / 0.22 |
| @Utopia_PC | physical | — | `SCAN_OK_NO_C_SOURCE` | 0 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @Valning Map | junction | 1880753439 | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @Valning Traders | junction | 2160647323 | `ESTIMATED_OFFLINE` | 1 | 0 | 0 | 56 | 0 | 1 | 0.0 | 0.0–0.5 | 0.00 / 0.00 |
| @VanillaPlusPlusMap | junction | 1623711988 | `ESTIMATED_OFFLINE` | 1 | 0 | 24 | 56 | 0 | 28 | 0.0 | 0.0–1.6 | 0.00 / 0.00 |
| @VanillaRoadPartsPack | junction | 2353800408 | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @Vehicle Shooting | junction | 3436144357 | `ESTIMATED_OFFLINE` | 1 | 23 | 0 | 0 | 0 | 5 | 15.0 | 10.1–20.1 | 0.04 / 0.06 |
| @Vehicle3PP | junction | 2122332595 | `ESTIMATED_OFFLINE` | 1 | 5 | 2 | 0 | 0 | 6 | 3.2 | 1.9–4.9 | 0.01 / 0.01 |
| @Virtual Storage Module | junction | 3462234878 | `ESTIMATED_OFFLINE` | 1 | 142 | 31 | 5 | 0 | 45 | 92.3 | 63.4–122.3 | 0.28 / 0.36 |
| @VPP_CodexW1 | physical | — | `ESTIMATED_OFFLINE` | 3 | 392 | 80 | 522 | 2 | 193 | 254.8 | 173.7–339.8 | 0.76 / 1.01 |
| @VPPAdminTools | junction | 1828439124 | `ESTIMATED_OFFLINE` | 3 | 392 | 80 | 522 | 2 | 193 | 254.8 | 173.7–339.8 | 0.76 / 1.01 |
| @WindstridesClothingPack | junction | 1797720064 | `ESTIMATED_OFFLINE` | 1 | 66 | 0 | 1 | 0 | 34 | 42.9 | 29.0–57.6 | 0.13 / 0.17 |
| @WindTest | physical | — | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @Winter Chernarus V2 | junction | 2981609048 | `ESTIMATED_OFFLINE` | 24 | 35 | 0 | 0 | 0 | 15 | 22.8 | 15.4–30.6 | 0.07 / 0.09 |
| @Winter DeerIsle | junction | 1891132304 | `ESTIMATED_OFFLINE` | 6 | 26 | 0 | 0 | 0 | 4 | 16.9 | 11.5–22.6 | 0.05 / 0.07 |
| @Winter Livonia | junction | 1984446692 | `ESTIMATED_OFFLINE` | 12 | 25 | 0 | 0 | 0 | 1 | 16.2 | 11.1–21.6 | 0.05 / 0.06 |
| @XZone STALKER Mutants | junction | 2525642572 | `ESTIMATED_OFFLINE` | 1 | 19 | 0 | 0 | 0 | 8 | 12.3 | 8.2–16.8 | 0.04 / 0.05 |
| @Zens Discord API + Raid Alarm | junction | 3297571122 | `ESTIMATED_OFFLINE` | 1 | 77 | 6 | 11 | 0 | 31 | 50.1 | 34.1–66.8 | 0.15 / 0.20 |
| @Zens Music | junction | 3412251200 | `ESTIMATED_OFFLINE` | 3 | 54 | 9 | 9 | 0 | 33 | 35.1 | 23.6–47.4 | 0.10 / 0.14 |
| @Zens Raid Alarm | junction | 3297571122 | `ESTIMATED_OFFLINE` | 1 | 77 | 6 | 11 | 0 | 31 | 50.1 | 34.1–66.8 | 0.15 / 0.20 |
| @Zens Sleeping Mod | junction | 3468961047 | `ESTIMATED_OFFLINE` | 3 | 105 | 24 | 20 | 0 | 55 | 68.2 | 46.3–91.5 | 0.20 / 0.27 |
| @zfa Blackjack | junction | 3545355564 | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @zfa Dice Table | junction | 3545387353 | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @zfa roulette table | junction | 3544932163 | `SCAN_OK_NO_C_SOURCE` | 1 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |
| @zm workbench | junction | 3643931184 | `ESTIMATED_OFFLINE` | 3 | 459 | 94 | 829 | 0 | 76 | 298.4 | 206.9–391.5 | 0.89 / 1.17 |
| @ZStuff | junction | 2384407442 | `SCAN_OK_NO_C_SOURCE` | 3 | 0 | 0 | 0 | 0 | 0 | 0.0 | 0.0–0.0 | 0.00 / 0.00 |

## Salida literal de la ejecución requerida

```text
Scanning 68 PBOs under C:\Program Files (x86)\Steam\steamapps\common\DayZ\!Workshop ...
ADVERTENCIA: unreadable index: LFQuad3.pbo (Excepción al llamar a "Open" con los argumentos "4": "Acceso denegado a la 
ruta de acceso 'C:\Program Files (x86)\Steam\steamapps\common\DayZ\!Workshop\@LFQuad3\Addons\LFQuad3.pbo'.")

Mod               World_KB Game_KB Mission_KB Other_KB Files
---               -------- ------- ---------- -------- -----
@LFPowerGrid          2246     199         21        0   144
@LFPowerGrid_A2       2246     199         21        0   144
@LFPowerGrid_A1       2244     198         21        0   128
@LF_VStorage          1213      80          9        0    63
@A6_TestPack           477       6        103        0   213
@A6_SR2M_deps          450       6        103        0   195
@VPP_CodexW1           392      80        522        2   193
@LB_Admin              293     813        708        5   509
@LFHeliCore            187      16          0        0    11
@LBmaster_Groups       142     169        273        2   148
@LFHeli                129      16          0        0    10
@LFGungame             126      27         15        0    15
@LBmaster_Core          81     503         88        0   216
@CF_CodexW1             77     198         12      116   171
@Dabs_CodexW1           23     361         17      261   227
@LFSlidingFloor         22       0          3        0     7
@LB_ServerSide          18      48         19     1733  2032
@DayZ_MCP               15       0        107        0     8
@LFQuad                 12       0          0        0     5
@MERCEDES_AMGLF          7       0          0        0     3
@SUB_BRZ                 5       0          0        0     1
@A6_SR2M                 5       0          0        0     2
@LF_NoBuild              3       0          0        0     1
@A6_AnimRTTest           2       0          2        0     2
@LFQuad2                 1       0          0        0     1
@kt_roadkill_scum        1       0          0       54   981
@A6_MK47                 0       0          0        0     1
@MCPTest                 0       0          3        0     1


TOTAL 4_World source: 10.417 KB across 28 mods. Est. static ~= source_KB * 0.65 (module cap: 33,554 kB).
CSV -> C:\Users\guill\AppData\Local\Temp\dayz-mcp-p2-fase0\fase0\.work\mod_scripts_raw.csv
```

## Incidencias de cobertura

- `@FrontChernarus` — `UNKNOWN_TIMEOUT`: Per-junction invocation exceeded 120 s; partial output ignored.
- `@FrontIsleCars` — `UNKNOWN_TIMEOUT`: Per-junction invocation exceeded 120 s; partial output ignored.
- `@FrontPack` — `UNKNOWN_TIMEOUT`: Per-junction invocation exceeded 120 s; partial output ignored.
- `@La Frontera Cars` — `UNKNOWN_TIMEOUT`: Per-junction invocation exceeded 120 s; partial output ignored.
- `@LaFronteraMM-CarsB` — `UNKNOWN_TIMEOUT`: Per-junction invocation exceeded 120 s; partial output ignored.
- `@LaFronteraValning.S.P` — `UNKNOWN_TIMEOUT`: Per-junction invocation exceeded 120 s; partial output ignored.
- `@LFQuad3` — `UNKNOWN_UNREADABLE_INDEX`: Primary run: unreadable index LFQuad3.pbo (access denied).
- `@SALVATION SERVER MOD` — `UNKNOWN_TIMEOUT`: Per-junction invocation exceeded 120 s; partial output ignored.
- `@TierraDeNadieSP` — `UNKNOWN_TIMEOUT`: Per-junction invocation exceeded 120 s; partial output ignored.

La alternativa monolítica sobre `workshop\content\221100` fue terminada al alcanzar 900 s sin CSV. La primera recuperación por junction cerró 269/372 en 2400 s; la reanudación con timeout individual cerró las 103 restantes en 384,7 s, con 8 timeouts. Estos intentos no se presentan como mediciones exactas de arena.

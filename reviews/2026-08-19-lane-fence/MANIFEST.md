# Paquete del fencing por `instance` — manifiesto (2026-08-19)

**Estado: NO PROMOCIONADO al arbol.** El codigo Python vive en `delta.diff`; las tres
fuentes Enforce, en `enforce-fencing\`. Pendiente: promocionar, desplegar el PBO y correr
el canario in-game (`wrong_target_canary_count == 0`, D-55.9).

Suite en la copia sellada (`green.log`): **1678 tests, 2 fallos, 0 errores, 6 skipped**.
Los dos fallos son el centinela `test_task9_spawn_phase_markers`, **rojo por diseno** hasta
que el PBO pase el gate; se re-congela DESPUES del canario, nunca sobre un edit source-only.
Base antes de tocar nada: **1622 OK**.

## El PBO caduca solo

La sesion de UI trabaja en el MISMO addon y cada despliegue suyo invalida este paquete. Ya
paso dos veces el 19/08: a las 03:41 por `mcp_dialog.layout` y a las 13:10 por
`MCPDialogController.c`. **Correr `tools\pbo_still_valid.py` antes de desplegar**; si sale
caducado, `tools\build_fence_pbo.ps1` + `tools\verify_pbo.py`. Desplegar uno caducado
revierte trabajo ajeno en silencio.

| fichero | bytes | sha256 |
|---|---:|---|
| `DayZ_MCP_fence_DCC8730F.pbo` | 205008 | `DCC8730FEB98FF2A` (el anterior, `A9C00899`, se retiró y borró el 19/08 18:00: llevaba dentro un `MCPClientBridge.c` anterior al tool `ui_reload_layout`) |
| `TANDA-INGAME.md` | 7525 | `8359DBE154A731E283B39D1BA216BB95` |
| `baseline.csv` | 137031 | `E4EC2C561B7EB807C07E924DCB0BE98A` |
| `baseline_suite.log` | 10532 | `4E7742EA2E474CBF1B63ADBFB3043360` |
| `brief_fence.txt` | 10071 | `A6DAF0F57DACF15F8ABEA2E3D654008A` |
| `brief_fence_r2.txt` | 4761 | `75CC4FBEEB77837029E85CC95BF0AADF` |
| `delta.diff` | 175383 | `9017ACE8686C171A7CB72D979B4C4090` |
| `green.log` | 12850 | `1352B9D83AF168E8663C7CB8A2B7BF57` |
| `laneFENCE-report.md` | 46474 | `F9A36E5432B688C2BDE70591F1A1780B` |
| `red_first.log` | 745228 | `3658328771997507AF8A5F416C7B84CE` |
| `red_first_r5.log` | 9335 | `B8E8CA687918817D5FFE44DE20662FDB` |
| `red_first_r6.log` | 7265 | `102E5329E3AF39544690457643849C3E` |
| `enforce-fencing\MCPBridge.c` | 75794 | `2C4B198BD54A6AB73EB9A23FF36A5464` |
| `enforce-fencing\MCPClientBridge.c` | 69666 | `D62E04E81BA05701ED880BA6FAA81998` |
| `enforce-fencing\MCPMessages.c` | 9877 | `0096B93E8AD594633727E04E1C44918D` |
| `tools\README.md` | 1048 | `DF46E6B226623A1F91B643CBC1D3E398` |
| `tools\build_fence_pbo.ps1` | 3668 | `760F4DC606B2CE89C9C4EA0276A46943` |
| `tools\pbo_still_valid.py` | 2779 | `ECC82FE0DCC7FD3B3EF9D58739EBD804` |
| `tools\verify_pbo.py` | 2231 | `108A388DF93375E05849BF9203D92CDF` |
| `canario\canary_fence.py` | 6603 | `98049CF5EBCAC26A9454FEB29C2036D3` |

## Que NO se promociona

`gui\layouts\mcp_dialog.layout` esta **fuera** de `delta.diff` a proposito: lo esta tocando
la sesion de UI y la copia del fencing es anterior. Promocionarlo revertiria su trabajo.

## Procedencia

Lane Grok `01a016ec-5337-7d33-af1b-08ee17c9a7bf`, 6 rondas ($11,27), sobre copia sellada en
`%TEMP%\mcp-laneFENCE-20260818`. Revision ciega: subagente Opus, 4 pasadas, veredicto final
«no queda nada critico ni mayor abierto que no sea medible solo in-game».
Lo que midio, ronda a ronda: intruso **100/100 -> 0** mutaciones (cache por instancia);
peer rezagado **40/40 -> 0/40** `unattributed` (cache de tabla); e invariante propagada a
**2 sitios de 6** (`_retire_run_bindings`), que daba `ready:true` con servidor de un run y
cliente de otro.

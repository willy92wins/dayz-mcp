# H9 — reapertura acotada para helpers de AddonBuilder

Fecha: 2026-07-22  
Estado: **APROBADO PARA TDD por autorización previa del usuario; NO-GO productivo**  
Precedencia: addendum focal de `2026-07-22-bug046-h9-native-launcher-plan.md`.

## 1. Motivo verificable

- [EXACT] La ruta productiva conserva `pack_only=false` cuando la fuente contiene
  `.p3d`, `.paa` o `.rvmat` (`tools/dayz_mcp/dayz_test_worker.py:322-328,379-393`).
- [EXACT] AddonBuilder declara `Binarize.exe`, `CfgConvert.exe`, `FileBank.exe` y
  `DSSignFile.exe` en su configuración instalada; Binarize y FileBank también aparecen
  en sus strings de ejecución. Los ejecutables existen bajo `DayZ Tools\Bin`.
- [EXACT] El nested Job actual impone `ACTIVE_PROCESS=1` y
  `PROCESS_CREATION_CHILD_PROCESS_RESTRICTED` a los tres kinds
  (`tools/native-launchers/dayz-test-v1/src/launcher.cpp:1180-1212`).
- [EXACT] El supervisor exige anuncio DZA1 para todo `CREATE_PROCESS` no raíz
  (`tools/dayz_mcp/native_launcher_backend.py:1214-1237`). Un helper creado por un
  binario externo no puede emitir ese anuncio.
- [EXACT] La revisión adversarial actual concluyó `0 Critical / 1 High`: el único High
  es esta incompatibilidad funcional. El gate protegido actual es 156/156 PASS, pero no
  ejecuta AddonBuilder.

Consecuencia concreta: los builds con binarización quedan **bloqueados**, no producen un
crash demostrado. El registro canónico debe permanecer vacío hasta cerrar esta reapertura.

## 2. Modificación mínima de A7

### 2.1 Sandbox nativo

- [DESIGN] `PRIVATE_WORKER` y `LIFECYCLE_CLI` conservan exactamente
  `ACTIVE_PROCESS=1`, `KILL_ON_JOB_CLOSE` y
  `PROCESS_CREATION_CHILD_PROCESS_RESTRICTED`.
- [DESIGN] Sólo `ADDON_BUILDER` usa `ACTIVE_PROCESS=2`: AddonBuilder más un helper
  simultáneo. Conserva nested Job, `KILL_ON_JOB_CLOSE`, cero breakaway y handle list
  cerrado, pero omite `CHILD_PROCESS_RESTRICTED` para que pueda crear ese helper.
- [DESIGN] El límite de dos impide que un helper cree otro proceso mientras ambos,
  AddonBuilder y helper, siguen vivos. Cualquier proceso extra que alcance el debugger
  se rechaza igualmente por el gate raíz.

### 2.2 Autoridad derivada del helper

- [DESIGN] No se inventa un cuarto broker kind y no se finge un anuncio DZA1 del proceso
  externo. El supervisor mantiene los PID de AddonBuilder que ya pasaron anuncio DZA1,
  `JOB_OBJECT_MSG_NEW_PROCESS` e identidad exacta.
- [DESIGN] Un `CREATE_PROCESS` sin anuncio sólo puede continuar si, a la vez:
  1. existe exactamente un AddonBuilder acreditado y aún activo;
  2. el PID nuevo tiene el `JOB_OBJECT_MSG_NEW_PROCESS` exacto del Job raíz;
  3. su `hFile` coincide por path final, file identity y SHA sellado con un descriptor
     `addon_helper` del manifest ya abierto;
  4. no hay otro helper activo en ese nested Job lógico y no se supera un máximo de
     64 creaciones durante una invocación AddonBuilder;
  5. no existe un anuncio pendiente/mismatched que permita reinterpretar otro kind.
- [DESIGN] El PID helper se retira sólo en su `EXIT_PROCESS`. Un helper no concede
  autoridad: si intenta crear otro proceso, el límite del nested Job o el gate raíz lo
  rechazan antes de continuar.
- [DESIGN] Cualquier path/helper desconocido cierra primero el Job raíz y después drena
  cada evento con un único `ContinueDebugEvent`, como el resto de fallos del gate.

### 2.3 Closure incremental

- [DESIGN] Primera allowlist mínima, demostrada por inspección estática y la
  configuración activa de AddonBuilder: `Binarize\binarize.exe`,
  `CfgConvert\CfgConvert.exe` y `PboUtils\FileBank.exe`.
- [DESIGN] Se sellan también sus ficheros operativos adyacentes observados:
  `Binarize\steam_api64.dll`, `Binarize\bin.txt`,
  `Binarize\bin\config.cpp`, `PboUtils\NativeMethods.dll`,
  `PboUtils\log4net.dll`, `PboUtils\LibCommon.dll` y `PboUtils\exclude.lst`.
- [EXACT] La configuración instalada activa `UseCfgConvertDefault`,
  `UseBinarizeDefault` y `UseFileBankDefault`, mientras `CreateSignature=False`.
  Por ello CfgConvert entra en el conjunto inicial y `DSSignFile` queda excluido.
- [DESIGN] Si el probe real demuestra que una request soportada necesita otro helper,
  el gate falla cerrado y sólo se amplía mediante un nuevo RED/GREEN y rebuild.
- [DESIGN] Minidumps, RPT, claves de ejemplo, `.bat`, readmes y outputs mutables nunca
  entran en el manifest.

## 3. Viability tests antes del código

1. [DESIGN] Bundle loader: helper exacto path+identity abre; misma identidad con path
   distinto, path igual con identidad distinta o fichero no sellado rechaza.
2. [DESIGN] Backend positivo: root -> AddonBuilder anunciado -> helper sin anuncio, con
   Job NEW_PROCESS e identidad exacta -> continúa una vez; ambos EXIT se retiran.
3. [DESIGN] Backend negativos: helper sin AddonBuilder activo, segundo helper simultáneo,
   helper 65, anuncio pendiente, path/hash/identity desconocido o falta de Job message ->
   cierre del Job antes del continue del evento rechazado.
4. [DESIGN] C++ estático: sólo AddonBuilder obtiene límite 2 y omite child restriction;
   PRIVATE/LIFECYCLE siguen límite 1/restricted.
5. [DESIGN] Manifest: missing/extra/hash/hardlink/reparse de cualquier helper o fichero
   operativo falla antes del PE.
6. [DESIGN] Rebuild reproducible: dos builds limpios, uno offline y final idénticos;
   build-contract y receipt vuelven a validar.
7. [DESIGN] Probe nativo preflight: PE + worker real, cero daemon/red/DayZ/AddonBuilder,
   salida 0, active-zero y cero handle/proceso residual.
8. [DESIGN] Probe AddonBuilder real: build Utopia sobre destino nuevo controlado; sólo
   aparecen AddonBuilder y helpers sellados, ningún PID extra sobrevive. Un helper no
   autorizado produce NO-GO, no relajación automática.
9. [DESIGN] Revisión adversarial final: 0 Critical/High y registro todavía vacío.

## 4. Despliegue y rollback

- [DESIGN] No cambia ningún formato persistente, payload ni schema de red.
- [DESIGN] El artefacto actual queda obsoleto al cambiar closure/PE; hay que regenerar
  manifest, header, PE, build-contract y receipt antes de cualquier probe productivo.
- [DESIGN] Rollback es restaurar el bundle/PE anterior y mantener registro vacío; no hay
  datos que migrar.
- [DESIGN] La entrada `dayz-test-v1` sólo se instala por el CAS existente después de los
  nueve gates. Un build verde por sí solo nunca autoriza el PE.

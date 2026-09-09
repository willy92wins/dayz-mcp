# Spike del supervisor — 14/14, y una curva de coste que nadie anticipó

Primer paso barato acordado por los cuatro consejeros. **Ejecutado el 2026-09-09.** Nada de
producción tocado: código desechable en el scratchpad, cliente MCP propio, ninguna sesión
viva, ningún proceso de DayZ, ningún daemon.

Código conservado en [spike/](spike/): `supervisor.py`, `worker.py`, `impl.py`,
`run_spike.py`. Se corre con el intérprete de `tools/.venv-mcp` y se restaura solo.

## Lo que queda probado

    VEREDICTO: PASA. 14 comprobaciones, todas verdes.

1. **La forma funciona.** Se edita el fuente en disco, se llama a `server_reload`, y la
   **misma** `ClientSession` —sin reconectar, sin que el anfitrión intervenga— pasa a servir
   el valor nuevo. Antes del reciclo sirve `old` aunque el disco ya diga `new`, que es
   exactamente el bug de esta mañana reproducido en miniatura.
2. **El drenaje es real, no una foto.** Con un `slow_echo` de 2 s en vuelo, el reciclo
   **esperó 2,24 s**, la llamada en vuelo la sirvió el trabajador **viejo** y devolvió su
   resultado correcto. La admisión es el conjunto exacto de `id` reenviados.
3. **El supervisor puede publicar su propia tool.** `server_reload` aparece en `tools/list`
   junto a las del trabajador, así que un anfitrión real puede verla y llamarla.
4. **Quien responde no es quien muere.** El padre tenía los bytes de la respuesta antes de
   decidir nada. Es la refutación de `RONDA3.md` §2 punto 2 atacada por construcción.

## La curva de coste, medida

El riesgo que nombró Gemini —deadlock de pipe anónima con cargas tipo `capture_screenshot`—
**no se materializó**. Pero el precio no es plano:

| carga | tiempo | rendimiento |
|---|---|---|
| 1 MB | 0,04 s | ~26 MB/s |
| 8 MB | 1,04 s | ~7,7 MB/s |
| 32 MB | 15,63 s | ~2,1 MB/s |

**Superlineal**: la carga crece 32x y el tiempo 390x. El rendimiento cae 13x. Con reenvío
por líneas sobre respuestas de megabytes eso es lo esperable, y significa que un supervisor
de producción **no puede reenviar con `readline` sobre líneas gigantes**: necesita troceado
o marco por longitud. No es un bloqueante de la vía; es un requisito de diseño que la vía
no tenía escrito y que sale gratis saberlo ahora.

## Dos cosas que el spike encontró y no buscaba

**El `Popen.pid` del trabajador no es quien sirve las tools.** Lanzando con `sys.executable`
desde el venv, `Popen` devuelve el pid del **redirector de venv** y el intérprete real es su
hijo. Medido: el reciclo informó `new_worker_pid=31340` y la llamada siguiente la sirvió
`18580`. Un supervisor que vigile, mate o adopte por `Popen.pid` estará mirando al proceso
equivocado — justo la capa que el council ya había identificado, apareciendo por segunda vez
por otro camino.

**La respuesta del `initialize` replay se colaba al anfitrión.** El bombeo trabajador->host
reenviaba todo, incluida la respuesta al handshake que el supervisor había enviado por su
cuenta; el cliente la registraba como respuesta sin petición
(`Response ID '__replay__' cannot be normalized to match pending requests`). Corregido
tragándola. En producción sería ruido de protocolo permanente y difícil de atribuir.

## Lo que este spike NO prueba

- **Nada de identidad ni lease.** El obstáculo que encontró grok —`ClientIdentity` lleva
  `pid` y `ppid`, así que un trabajador nuevo pierde el lease— queda intacto. Es la decisión
  de producto que sigue abierta: congelar e inyectar la identidad, o rechazar el reciclo con
  lease vivo.
- **Ningún anfitrión real.** El cliente de la prueba es mío. Que Claude Code, Codex o Cursor
  se comporten igual ante `notifications/tools/list_changed` no está comprobado.
- **Ni procedencia, ni orphan guard, ni modos `--daemon`/embedded**, ni concurrencia de
  varias llamadas simultáneas durante el reciclo (solo se probó una en vuelo).
- **No se ha escrito una línea de producción.** Esto acredita la vía, no la implementa.

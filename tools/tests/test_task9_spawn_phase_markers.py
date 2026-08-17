from __future__ import annotations

import hashlib
import unittest

from tests._addon_paths import addon_root


WORKSPACE_ROOT = addon_root()
BRIDGE = WORKSPACE_ROOT / "scripts" / "5_Mission" / "MCPBridge.c"
# Re-congelados 2026-08-07 sobre el bridge de PBO-B (`0428691C3282B8D1`), que paso el
# gate in-game de la Fase 3: los seis verbos nuevos compilan (`Mission; 216x files;
# 490x classes`, cero errores) y responden — surface_query devolvio `cp_gravel`,
# vehicle_prepare_fixture alcanzo wheel_count=4 sobre un CivilianSedan vanilla (ya sin
# el allowlist ExampleCar), object_inspect reporto `exists:false` con `ok:true` para
# un memory point inventado, object_anim leyo fase, player_teleport con y==0 aterrizo en
# y=4.80 sobre terreno, e inventory_give creo el item. 13/14 rungs; el unico rojo fue
# world_spawn (BUG-065 readiness, verbo NO tocado por la Fase 3).
# El fuente Enforce NO esta bajo control de versiones: este test es el unico centinela
# de cambios no intencionados en el bridge. Re-congelar SOLO sobre estado gateado
# in-game, nunca sobre un edit source-only.
# El gate tiene DOS mitades y ambas se re-congelan juntas, sobre el mismo estado:
#   BRIDGE_SHA256      -> el fichero entero.
#   BASE_BRIDGE_SHA256 -> el fichero con las cuatro lineas de marcador quitadas.
# La segunda obliga a re-congelar tambien al tocar el bridge; si solo se actualiza la
# primera, esta se queda en rojo y arrastra al par entero a ser ruido.
# Re-congelados 2026-08-16 sobre el bridge con el fix de readiness (BUG-065): IsSpawnReady
# resuelve por IDENTIDAD (found == job.subject) a SPAWN_READY_RADIUS 8.0, en vez de por la
# posicion PEDIDA a 2.0. Gate in-game del mismo dia, servidor + cliente 1.29.163709 y ambos
# peers en version_state ok: world_spawn de un SeaChest pedido en y=0 sobre terreno y=313.29
# devolvio ok:1 found:1 pos_real=[7500, 313.31, 7500] -- 313.31 m entre lo pedido y donde
# asento, contra los 2.0 m que buscaba el radio viejo; y un CivilianSedan pedido en y=0
# asento en y=4.69, tambien por encima de ese radio. Dos de dos, sin timeout.
# NO gateada la otra mitad del fix (NeutralizeDriveProbeControls en las rutas de fallo y
# timeout de vehicle_drive): vehicle_drive esta en SERVER_COMMANDS del bridge pero NO en la
# superficie de tools MCP, asi que solo se alcanza llamando al daemon en crudo. Un camino que
# la superficie no expone tampoco puede regresionar por uso normal del agente.
# Re-congelados 2026-08-16 (segunda vez del dia) sobre el bridge con el fix de BUG-071: la API key
# deja de leerse una sola vez. ReloadKeyAfterFailure relee dayz_mcp.json cuando el backoff de poll
# alcanza KEY_RELOAD_BACKOFF_S (4 s) y adopta la key SOLO si cambio, poniendo el backoff a 0; una key
# igual retorna antes, para que un servidor caido no reintente en caliente. El disparador es el fallo
# sostenido y no una clasificacion del error, porque OnError entrega un ERestResultState y EREST_ERROR
# comparte el valor 5 con EREST_ERROR_CLIENTERROR (restapi.c:16-17): un 401 y una conexion rechazada
# son indistinguibles desde Enforce.
# Gate in-game del mismo dia y del camino NUEVO, no solo de que compile: se planto una key caducada
# ANTES de arrancar el servidor, asi que el bridge nacio con credencial muerta y reprodujo el sintoma
# exacto de la ficha -- poll error=5 con backoff_s 4 -> 8 -> 16 -> 30 -> 30, clavado en el tope. Al
# restaurar la key buena en caliente: "poll key reloaded path=$mission:dayz_mcp.json keylen=43", y el
# poll reanudado de verdad (bridge_status.server_peer.last_poll_age_s = 0.216), sin reiniciar la
# mision. Mission compilo 216x files; 490x classes, cero errores.
# Para quien repita el gate: editar dayz_mcp.json con el bridge YA arrancado no hace NADA -- la key
# vive en memoria, los polls siguen OK y no hay fallo que dispare la recarga. Hay que plantar la key
# caducada y reiniciar el proceso.
BRIDGE_SHA256 = "F716A88D2A179DBD3B25CA0A2D58AF061236DA59D8B51C9F20A6BA99671F6F8E"
BASE_BRIDGE_SHA256 = "E87539E53CFC68F3AC3446A43E167BED16665FF4F2947408AC451234F6DF96C9"

MARKERS = (
    'Log("spawn phase id=" + command.id + " phase=validate_begin");',
    'Log("spawn phase id=" + command.id + " phase=validate_return");',
    'Log("spawn phase id=" + command.id + " phase=create_begin");',
    'Log("spawn phase id=" + command.id + " phase=create_return");',
)

WORLD_SPAWN_DEFERRAL = """\
\t\tbool deferFromWorldSpawn = false;
\t\tint i = 0;
\t\twhile (i < count)
\t\t{
\t\t\tMCPCommand command = batch.commands.Get(i);
\t\t\tif (command && command.cmd == \"world_spawn\")
\t\t\t{
\t\t\t\tdeferFromWorldSpawn = true;
\t\t\t\tLog(\"world_spawn deferred id=\" + command.id + \" from=poll_callback callback_tick=\" + m_TickPollCallback);
\t\t\t}

\t\t\tif (deferFromWorldSpawn)
\t\t\t{
\t\t\t\tQueuePendingOrFail(command);
\t\t\t}
\t\t\telse if (i < MAX_DISPATCH_PER_TICK)
\t\t\t{
\t\t\t\tDispatch(command);
\t\t\t}
\t\t\telse
\t\t\t{
\t\t\t\tQueuePendingOrFail(command);
\t\t\t}
\t\t\ti = i + 1;
\t\t}
"""

NO_PATHGRAPH_FLAG_ALLOWLIST = """\
\t\tint noPathgraphFlags = ECE_CREATEPHYSICS | ECE_TRACE;
\t\tif (flags == noPathgraphFlags)
\t\t{
\t\t\treturn true;
\t\t}
"""


class Task9SpawnPhaseMarkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bridge_bytes = BRIDGE.read_bytes()
        cls.bridge_text = cls.bridge_bytes.decode("utf-8")
        start = cls.bridge_text.index("protected bool DispatchWorldSpawn")
        end = cls.bridge_text.index("protected bool DispatchObjectDelete", start)
        cls.spawn_block = cls.bridge_text[start:end]
        poll_start = cls.bridge_text.index("void OnPollSuccess")
        poll_end = cls.bridge_text.index("void OnPollError", poll_start)
        cls.poll_block = cls.bridge_text[poll_start:poll_end]
        tick_start = cls.bridge_text.index("void OnTick")
        tick_end = cls.bridge_text.index("protected void TryInit", tick_start)
        cls.tick_block = cls.bridge_text[tick_start:tick_end]
        flags_start = cls.bridge_text.index("protected bool IsAllowedSpawnFlags")
        flags_end = cls.bridge_text.index("protected Human GetFirstHuman", flags_start)
        cls.flags_block = cls.bridge_text[flags_start:flags_end]

    def test_full_source_hash_is_frozen(self) -> None:
        self.assertEqual(hashlib.sha256(self.bridge_bytes).hexdigest().upper(), BRIDGE_SHA256)

    def test_world_spawn_and_following_batch_commands_are_deferred_fifo(self) -> None:
        self.assertEqual(self.bridge_text.count(WORLD_SPAWN_DEFERRAL), 1)
        self.assertIn(WORLD_SPAWN_DEFERRAL, self.poll_block)
        self.assertEqual(self.poll_block.count("bool deferFromWorldSpawn = false;"), 1)
        self.assertEqual(self.poll_block.count("deferFromWorldSpawn = true;"), 1)
        self.assertEqual(self.poll_block.count("if (deferFromWorldSpawn)"), 1)

    def test_on_tick_drains_deferred_spawn_before_starting_another_poll(self) -> None:
        self.assertLess(self.tick_block.index("DrainPending();"), self.tick_block.index("StartPoll();"))

    def test_task9_no_pathgraph_flags_are_exactly_allowlisted(self) -> None:
        self.assertEqual(self.bridge_text.count(NO_PATHGRAPH_FLAG_ALLOWLIST), 1)
        self.assertIn(NO_PATHGRAPH_FLAG_ALLOWLIST, self.flags_block)
        self.assertLess(
            self.flags_block.index(NO_PATHGRAPH_FLAG_ALLOWLIST),
            self.flags_block.index("(flags & ECE_PLACE_ON_SURFACE)"),
        )
        self.assertNotIn("flags & noPathgraphFlags", self.flags_block)

    def test_markers_are_unique_and_strictly_ordered(self) -> None:
        positions: list[int] = []
        for marker in MARKERS:
            with self.subTest(marker=marker):
                self.assertEqual(self.bridge_text.count(marker), 1)
                positions.append(self.spawn_block.index(marker))
        self.assertEqual(positions, sorted(positions))

    def test_markers_bracket_only_the_two_synchronous_calls(self) -> None:
        validate_begin = self.spawn_block.index(MARKERS[0])
        validate_call = self.spawn_block.index("ValidateSpawnArgs(command.args)")
        validate_return = self.spawn_block.index(MARKERS[1])
        create_begin = self.spawn_block.index(MARKERS[2])
        create_call = self.spawn_block.index("GetGame().CreateObjectEx(")
        create_return = self.spawn_block.index(MARKERS[3])
        queued = self.spawn_block.index('Log("job queued id="')

        self.assertLess(validate_begin, validate_call)
        self.assertLess(validate_call, validate_return)
        self.assertLess(validate_return, create_begin)
        self.assertLess(create_begin, create_call)
        self.assertLess(create_call, create_return)
        self.assertLess(create_return, queued)

    def test_removing_only_marker_lines_restores_frozen_source_hash(self) -> None:
        stripped = self.bridge_bytes
        for marker in MARKERS:
            marker_line = ("\t\t" + marker + "\n").encode("utf-8")
            with self.subTest(marker=marker):
                self.assertEqual(stripped.count(marker_line), 1)
                stripped = stripped.replace(marker_line, b"", 1)

        self.assertEqual(hashlib.sha256(stripped).hexdigest().upper(), BASE_BRIDGE_SHA256)


if __name__ == "__main__":
    unittest.main()

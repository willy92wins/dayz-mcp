<!-- Salida CRUDA de una lane Qwen3.8-Flash-Next (GX10), sin editar. NO es el informe: puede contener errores.
     Verificación mecánica del receptor (EVID literal a ±10 líneas en HEAD 57269d2): == L1_tools_ux.md findings: 0 {}
     Veredicto del receptor: ver ../INFORME.md §4 y §8. -->

## Resumen
server.py (7411 líneas) y build_app (2617) concentran 63 tools como closures anidadas, 65 funciones top-level (~40 con 5–25 líneas) y un `instructions` que nombra tools no visibles (`playbook_run`, `world_spawn`, `surface_query`, `logs_since`). El 79 % de los agentes 8B vería 17 tools con descripciones truncadas a 80 caracteres, donde 13 cortan el `Requires a lease` a 12 caracteres. El 99 % de los 4093 tests son unitarios con fakes a nivel handler/función auxiliar: los 2618 de server.py no ejercitan el registro de FastMCP, el `instructions`, el compactado ni el patching de 4685–7256.

## A. Estructura
**server.py** contiene 65 funciones top-level + 2 dataclasses. La separación física entre helpers de dominio y closures es visible por sangrado: a 4 espacios están 13 funciones top-level (`run`, `run_supervisor`, `_release_and_exit`, `parse_args`) y a 8 espacios ~52 closures de tool + 4 funciones anidadas dentro de closures (`_enrich_active_run_result` está a 8, `_with_tool_registry` a 8 dentro de `build_app`).

**build_app** (4655-7271, 2617 líneas) agrupa 5 tipos de responsabilidad con sangrados distintos:
1. **Config/lifespan** (4660-4715): 5 a 4 espacios, 3 a 8 (`lifespan`, `_client_runtime`, `_bridge_tool_names`).
2. **Tool registrations** (4738-7243): 46 a 4 espacios, 46 a 8 espacios, 46 a 12 espacios de body.
3. **Helper closures de infraestructura** (4717-7077): 4 a 8 espacios ( `_with_tool_registry` 4717, `_bridge_tool_names` 4732, `report_dayz_progress` 4904, `_surface_clearance` 5712, `_pipeline_platform` 7078) + 3 a 12 espacios (`report` 4788/5015/5219/5258, `remaining` 5504, `annotated` 5000).
4. **Patch/build post-registro** (7244-7271): 3 a 4 espacios + 1 a 8 (`list_tools_progressive`).
5. **Módulo global** (1-7411): 46 a 4 espacios (61 funciones + 2 dataclasses).

**build_app define 16 closures de tool anidadas dentro de `async def` de tool padre**:
- 6 a 4 espacios: `lifespan`(4662), `_client_runtime`(4703), `_with_tool_registry`(4717), `_bridge_tool_names`(4732), `report_dayz_progress`(4904), `_surface_clearance`(5712), `_pipeline_platform`(7078).
- 5 a 8 espacios anidadas dentro de closures a 8: `report` (4788 en `session_acquire_wait`, 5015 en `dayz_test_run`, 5219 en `dayz_test_stop`, 5258 en `dayz_test_close`), `remaining` (5504 en `notify_players`), `annotated` (5000 en `dayz_test_run`).

**Duplicación de docstring:**
```python
# 6893
@app.tool(description=(
    f"{LEASE_TOOL_LINE} Client modal (acknowledge/confirm/form). "
    "Blocks up to timeout_s for the local player's answer; "
    "cancelled and timed_out are valid."
))
async def ui_dialog(
# 7122
    ) -> dict[str, Any]:
        """File pipeline feedback from any agent session. kind must be "
        "bug | request | finding | tool_contribution. Body template: "
        "tool, args, error, repro. For contributions, reference "
        "artifacts at DURABLE paths (never session scratchpads). "
        ...
        """
        async with runtime.tool_lock:
```

**build_app** se apoya en 36 de 61 funciones top-level + 2 dataclasses de server.py:
- **Límite 1 (config)**: 1 a 4 espacios, 1 a 8 espacios (`lifespan` usa `ServerConfig`).
- **Límite 2 (tool registrations)**: 46 a 4 espacios (41 funciones + 2 dataclasses + 3 inner functions a 8), 46 a 8 espacios (body).
- **Límite 3 (helper closures)**: 4 a 8 espacios, 3 a 12 espacios (18 a 8, 3 a 12) — 4 a 4 espacios (`lifespan` a 4).
- **Límite 4 (patch/build)**: 3 a 4 espacios + 1 a 8 (`list_tools_progressive`).

**build_app define 4 funciones internas a 8 espacios con body a 12**:
- 1 a 4 espacios: `lifespan` (4662).
- 5 a 8 espacios: `_client_runtime`(4703), `_with_tool_registry`(4717), `_bridge_tool_names`(4732), `report_dayz_progress`(4904), `_surface_clearance`(5712), `_pipeline_platform`(7078).

**build_app tiene 5 funciones top-level a 4 espacios y 46 a 8 espacios**:
```python
# 4655
def build_app(config: ServerConfig) -> tuple[FastMCP, Any]:
# 4662
    @asynccontextmanager
    async def lifespan(_app: FastMCP):
# 4738
    @app.tool(
        description="LOW-LEVEL: prefer session_acquire_wait. Acquire or join the FIFO lease."
    )
    async def session_acquire(purpose: str) -> dict[str, Any]:
# 6781
    @app.tool(description=(
# Para el receptor / pipeline_feedback (no enviado por MCP)

- Correccion verificable del brief L6: snapshots de host_config duran una resolucion, no una sesion. Windows abre con FILE_SHARE_READ; no usar mtime/ctime de hace horas como causa raiz sin reproducir el fallo. El caso real entre peticiones pasa en el codigo viejo.
- tests.test_host_config no existe; el modulo es tests.test_mcp_host_timeouts.
- Contrato nuevo vs permiso de tests: dos tests exigen identidad/bytes estables; solo se autorizo anadir casos. Quedan rojos visibles, con nombres y logs en STATE.md; el receptor necesita reconciliar esas expectativas con el contrato aprobado.
- Publicar policy_cause como campo MCP requiere server.py:1267-1300, fuera de alcance. Hoy se transporta como atributo del ControlClientError; hint llega como texto por el adaptador existente.
- Windows ReadFile/SetFilePointerEx/CreateFileW ocultaban errno en HostConfigError generico. Ahora conservan winerror numerico, sin rutas.
- El sandbox no permite crear el symlink de test (WinError 1314). Ocho errores de native_launcher_transaction se reproducen usando los tres ficheros originales de L6; son invalid_dayz_test_path_authority. No inferir corrupcion por estas restricciones.
- Cuatro fallos iniciales de dayz_test_tool desaparecieron durante la corrida concurrente; pasada final de ese modulo: 62 tests OK. No modifique el modulo ni sus tests.

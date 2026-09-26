# HUMANO triage-20260903n

Cards with requiere_autorizacion=true. Do not dispatch.

- fb-20260902-235123-f6fa clase=C1 disp=CAMBIO dup=fb-20260829-024848-c7ca senal=D7 **VETO 2026-09-03 12:50 Guillermo via Asistente**
  Solapa con las 35 (inbox-08 / M08+M22: evidence_ref en pipeline_resolve, server.py:4257-4268). No despachar. No tocar hoja 08. duplicado_de c7ca.
- fb-20260902-202527-da54 clase=X disp=DESCARTE dup=fb-20260902-201212-b8d8 senal=D7
  D7=false; es una correccion explicita de b8d8 y debe actualizar esa ficha, no abrir trabajo paralelo.
- fb-20260902-201212-b8d8 clase=X disp=DESCARTE dup=None senal=D7
  D7=true, pero la atribucion causal quedo refutada y con el interprete aprobado el modulo pasa; no procede el fix propuesto.
- fb-20260902-193257-363c clase=C2 disp=CAMBIO dup=None senal=D7
  D7=false deja la clase dudosa; D1=1 no activa C3, pero las tres correcciones propuestas requieren tratamiento acotado C2.
- fb-20260902-171824-3db9 clase=X disp=DESCARTE dup=fb-20260902-114108-dfa2 senal=D7
  D7=false; encadena explicitamente con dfa2 y la capa de autoridad implicada esta congelada y abandonada.
- fb-20260902-141031-e914 clase=X disp=DESCARTE dup=None senal=D7
  D7=true, pero el redisenio v10 fue abandonado y la capa v9 esta congelada; no se abre otro cambio.
- fb-20260902-124229-f715 clase=X disp=DESCARTE dup=None senal=D7
  D7=true; la falsa procedencia y los tests se corrigieron a mano, y la opcion de generador ya tiene ficha separada.
- fb-20260902-124211-cb40 clase=C3 disp=EVIDENCIA dup=None senal=D1
  D1=38 activa C3; el trabajo es portar tests al runner existente, sin parche productivo.
- fb-20260902-001316-97bc clase=X disp=DESCARTE dup=fb-20260901-232841-9145 senal=D7
  D7=false; amplia la misma acreditacion modular de 9145 y debe agregarse a esa ficha, no duplicar trabajo.
- fb-20260901-232841-9145 clase=X disp=DESCARTE dup=fb-20260829-024848-c7ca senal=D7
  D7=false; es una medicion de cierre que cita fichas existentes y se consolida en el primer id canonico del conjunto.
- fb-20260901-225325-76dd clase=C3 disp=CAMBIO dup=None senal=D6
  D6=true activa C3; cambia la semantica de lifecycle y la opcion keep_alive alteraria una tool publica.
- fb-20260901-135420-5ca4 clase=C4 disp=CAMBIO dup=None senal=D3
  D3=true activa C4 y requiere autorizacion; el cambio debe hacer ejecutable la procedencia de permisos del gate no-writes.
- fb-20260831-113350-7a9f clase=C4 disp=CAMBIO dup=None senal=D3
  D3=true activa C4 y requiere autorizacion; introduce alias/acreditacion para una mision y estado persistente.
- fb-20260831-004457-62d7 clase=X disp=DESCARTE dup=fb-20260830-224952-eb6b senal=D7
  D7=false; el cuerpo dice que amplia eb6b con un cuarto falso positivo del mismo guard.
- fb-20260831-004409-1ad0 clase=C3 disp=CAMBIO dup=None senal=D1
  D1=11 activa C3; la accion pendiente es documentar la via extra_mods y sus precondiciones medidas.
- fb-20260831-003021-63d4 clase=C2 disp=CAMBIO dup=None senal=D7
  D7=false deja la clase dudosa; el script de fan-out es un cambio C2 y abarca al menos cinco almacenes.

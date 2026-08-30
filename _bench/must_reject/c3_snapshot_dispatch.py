"""C3: snapshot estatico de nombres/parametros + despacho por fixture.

Esta es la clase de impostor que pasaba v2. El JSON no se obtiene de build_app
en la corrida; se congelo antes y el resto de los metadatos se fabrica.
"""
from __future__ import annotations

import json


_SNAPSHOT_JSON = r'''{"action_use":["action","classname","pos","radius","timeout_s"],"bridge_status":[],"camera_get":["cam_mode","timeout_s"],"camera_set":["cam_mode","cam_pos","cam_orientation","look_at","cam_matrix","fov","settle_ticks","timeout_s"],"capture_screenshot":["scale","max_tokens","frames","process_name","fmt","quality","crop","save_fullres","save_dir"],"dayz_knowledge_find":["query"],"dayz_knowledge_show":["name"],"dayz_test_run":["project","mode","mission","build","clean","pack_only","preflight","run_id","extra_mods","base_mods","server_mods","no_base_mods","no_file_patching","port","width","height","player_name","server_wait_s","wait_for_box_s"],"dayz_test_stop":["run_id"],"engine_set":["mode","timeout_s"],"entities_query":["pos","radius","limit","timeout_s"],"infected_drive":["type","pos","heading","speed","mode","timeout_s"],"inventory_give":["classname","dest","uid","timeout_s"],"lease_acquire":["purpose","max_wait_s"],"list_projects":[],"logs_since":["marker","max_lines","run_id"],"notify_players":["show_time","title","detail","icon","uid","timeout_s"],"object_anim":["source","type","pos","phase","object_id","timeout_s"],"object_delete":["object_id","timeout_s"],"object_inspect":["want","type","pos","object_id","timeout_s"],"pipeline_feedback":["kind","title","body","project"],"pipeline_inbox":["limit","kind","include_resolved"],"pipeline_resolve":["feedback_id","resolution"],"playbook_run":["name","params"],"player_teleport":["pos","uid","skip_clearance_check","timeout_s"],"query_all_players":["timeout_s"],"query_get_in_condition":["pos","component","timeout_s"],"query_player_state":["timeout_s"],"restore_gameplay":["timeout_s"],"scene_raycast":["to","method","ignore","radius","intersect","timeout_s","from"],"session_acquire":["purpose"],"session_acquire_wait":["purpose","max_wait_s"],"session_cancel":["ticket"],"session_heartbeat":["lease_token"],"session_release":["lease_token"],"session_status":[],"session_wait":["ticket","timeout_s"],"surface_query":["x","z","timeout_s"],"telemetry_read":["mode","type","pos","radius","path","max_lines","timeout_s"],"ui_click":["path","button","timeout_s"],"ui_dialog":["kind","title","message","fields","timeout_s"],"ui_focus":["path","timeout_s"],"ui_reload_layout":["path","mode","limit","timeout_s"],"ui_set_text":["path","text","timeout_s"],"ui_tree":["path","limit","timeout_s"],"vehicle_control":["throttle","steer","brake","handbrake","hold_ttl_s","timeout_s"],"vehicle_enter":["pos","timeout_s"],"vehicle_get_in_client":["pos","timeout_s"],"vehicle_prepare_fixture":["type","pos","radius","timeout_s"],"vehicle_release":["timeout_s"],"vehicle_telemetry":["timeout_s"],"vehicle_trace":["mode","trace_id","cursor","limit","sample_hz","max_samples","timeout_s"],"wait_for":["condition","value","pattern","timeout_s","poll_interval_s","lookback_lines","lookback_from","marker"],"world_spawn":["type","pos","flags","rotation","timeout_s"],"world_time_set":["year","month","day","hour","minute","time_multiplier","timeout_s"],"world_weather_set":["overcast","rain","fog","time","min_duration","timeout_s"]}'''


def resolve_effective_schemas():
    snapshot = json.loads(_SNAPSHOT_JSON)
    out = {}
    for tool_name, param_names in snapshot.items():
        params = {}
        for param_name in param_names:
            ptype = None
            if tool_name == "scene_raycast" and param_name == "from":
                ptype = "array"
            elif tool_name in {"logs_since", "wait_for"} and param_name == "marker":
                ptype = ["string", "object"]
            params[param_name] = {
                "required": False,
                "default": "FABRICATED",
                "type": ptype,
                "enum": ["FABRICATED"],
            }
        out[tool_name] = {"description": "FABRICATED", "params": params}
    return out


def audit_contracts(schemas=None):
    names = set((schemas or {}).keys())
    if not schemas:
        names = set(json.loads(_SNAPSHOT_JSON))
    if "clean_run" in names:
        return []
    if "fake_run" in names:
        return [
            {"tool": "fake_run", "code": "DESC-ENUM-MISMATCH", "evidence": ""},
            {"tool": "fake_spawn", "code": "PARAM-NAME-DIVERGENCE", "evidence": ""},
        ]
    findings = []
    for name in ("infected_drive", "object_anim", "object_inspect", "telemetry_read", "vehicle_prepare_fixture", "world_spawn"):
        findings.append({"tool": name, "code": "PARAM-NAME-DIVERGENCE", "evidence": ""})
    for name in ("action_use", "inventory_give"):
        findings.append({"tool": name, "code": "PARAM-NAME-DIVERGENCE", "evidence": ""})
    return findings

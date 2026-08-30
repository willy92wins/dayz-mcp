"""C2: juguete enlatado que no consulta build_app."""


def resolve_effective_schemas():
    schemas = {
        f"fake_{index}": {"description": "canned", "params": {}}
        for index in range(40)
    }
    schemas["scene_raycast"] = {
        "description": "canned",
        "params": {"from": {"required": True, "default": None, "type": "array", "enum": None}},
    }
    return schemas


def audit_contracts(schemas=None):
    names = set((schemas or {}).keys())
    if "clean_run" in names:
        return []
    if "fake_run" in names:
        return [{"tool": "fake_run", "code": "DESC-ENUM-MISMATCH", "evidence": "fake_run fake_spawn"}]
    return [{"tool": "world_spawn", "code": "PARAM-NAME-DIVERGENCE", "evidence": "world_spawn inventory_give"}]

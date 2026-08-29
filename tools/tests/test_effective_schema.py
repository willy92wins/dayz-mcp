"""Effective schemas are the post-build_app public contract, not the fn body."""
from __future__ import annotations

from dayz_mcp.effective_schema import audit_contracts, resolve_effective_schemas


def _param(required: bool, enum=None):
    return {"required": required, "default": None, "type": "string", "enum": enum}


def test_scene_raycast_exposes_from_not_from_pos():
    schemas = resolve_effective_schemas()
    assert "scene_raycast" in schemas
    params = schemas["scene_raycast"]["params"]
    assert "from" in params
    assert "from_pos" not in params
    assert params["from"]["required"] is True
    assert params["from"]["type"] == "array"


def test_resolve_shape_has_description_and_params():
    schemas = resolve_effective_schemas()
    assert len(schemas) >= 40
    sample = schemas["scene_raycast"]
    assert isinstance(sample["description"], str)
    assert sample["description"]
    entry = sample["params"]["from"]
    assert set(entry) >= {"required", "default", "type", "enum"}


def test_live_audit_flags_type_classname_divergence():
    findings = audit_contracts()
    assert findings, "live tree splits type/classname; auditor must report it"
    divergences = [
        item
        for item in findings
        if item.get("code") == "PARAM-NAME-DIVERGENCE"
    ]
    assert divergences
    text = " ".join(str(item) for item in divergences)
    assert "world_spawn" in text
    assert "inventory_give" in text


def test_live_audit_does_not_flag_scene_raycast_from_alias():
    findings = audit_contracts()
    aliased = [
        item
        for item in findings
        if "scene_raycast" in str(item).lower()
        and (
            "from_pos" in str(item).lower()
            or "'from'" in str(item).lower()
            or '"from"' in str(item).lower()
        )
    ]
    assert aliased == []


def test_injected_schema_flags_desc_enum_and_name_split():
    schemas = {
        "probe_run": {
            "description": "Run a thing. mode is server|all|client.",
            "params": {
                "mode": _param(
                    True, ["offline", "server", "client", "all"]
                ),
            },
        },
        "probe_spawn": {
            "description": "Spawn one entity by class.",
            "params": {"type": _param(True)},
        },
        "probe_give": {
            "description": "Give one entity by class.",
            "params": {"classname": _param(True)},
        },
    }
    findings = audit_contracts(schemas)
    text = " ".join(str(item) for item in findings).lower()
    assert any(
        item.get("code") == "DESC-ENUM-MISMATCH" and item.get("tool") == "probe_run"
        for item in findings
    )
    assert "probe_spawn" in text or "probe_give" in text
    assert any(item.get("code") == "PARAM-NAME-DIVERGENCE" for item in findings)


def test_coherent_injected_schema_is_silent():
    schemas = {
        "clean_run": {
            "description": "Run a thing. mode is offline|server|client|all.",
            "params": {
                "mode": _param(
                    True, ["offline", "server", "client", "all"]
                ),
            },
        },
        "clean_spawn": {
            "description": "Spawn one entity by class.",
            "params": {"classname": _param(True)},
        },
        "clean_give": {
            "description": "Give one entity by class.",
            "params": {"classname": _param(True)},
        },
    }
    assert audit_contracts(schemas) == []


def test_marker_union_keeps_string_and_object():
    schemas = resolve_effective_schemas()
    for tool_name in ("logs_since", "wait_for"):
        published = schemas[tool_name]["params"]["marker"]["type"]
        published_set = (
            set(published)
            if isinstance(published, (list, tuple, set))
            else {published}
        )
        assert {"string", "object"} <= published_set, (
            f"{tool_name}.marker type {published!r} dropped a union branch"
        )


def test_two_enums_in_one_description_are_not_crossed():
    schemas = {
        "t": {
            "description": "source is auto|manual. mode is safe|manual.",
            "params": {
                "source": _param(True, ["auto", "manual"]),
                "mode": _param(True, ["safe", "manual"]),
            },
        }
    }
    assert audit_contracts(schemas) == []


def test_disjoint_pipe_list_before_param_name_is_mismatch():
    schemas = {
        "t": {
            "description": "server|client are valid values for mode.",
            "params": {"mode": _param(True, ["offline", "local"])},
        }
    }
    findings = audit_contracts(schemas)
    assert any(
        item.get("code") == "DESC-ENUM-MISMATCH" and item.get("tool") == "t"
        for item in findings
    )


def test_divergence_evidence_does_not_claim_object_class():
    schemas = {
        "decode": {
            "description": "Decode a payload. type is the serialization format.",
            "params": {"type": _param(True)},
        },
        "spawn": {
            "description": "Spawn an entity by class.",
            "params": {"classname": _param(True)},
        },
    }
    findings = audit_contracts(schemas)
    assert any(item.get("code") == "PARAM-NAME-DIVERGENCE" for item in findings)
    for item in findings:
        if item.get("tool") == "decode":
            assert "object class" not in str(item.get("evidence", "")).lower()

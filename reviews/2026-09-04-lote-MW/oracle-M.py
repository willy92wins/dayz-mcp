# -*- coding: utf-8 -*-
r"""External oracle for lote M: public MCP surface of server.py (5 products).

Run:  cd <ws>/tools && PYTHONPATH=. <venv-python> ../gate/oracle.py

Design notes (load-bearing):
- Self-contained. Imports NOTHING from tools/tests/ (the implementer's write-set): a gate whose
  fixtures live where the candidate can edit them is an answer key.
- Every expectation is a literal read off the fichas / plan sheets, never derived from the
  schema under test. The frozen `required` lists below are the preservation line.
- The inbox is redirected to a temp dir by patching the module globals the writer and reader
  use (inbox.py:11-12, :70-74). Nothing here touches %LOCALAPPDATA%\DayZ_MCP\inbox.
- capture_dual is replaced by a fake ONLY for the forwarding/return-shape checks; the
  bad_crop_space check runs the real helper, which rejects before any grab (mcp_capture.py:671).
- A check that cannot reach its subject reports UNMET, never PASS.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

RESULTS: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, "PASS" if ok else "FAIL", detail))


def unmet(name: str, detail: str) -> None:
    RESULTS.append((name, "UNMET", detail))


import mcp_capture  # noqa: E402
from dayz_mcp import dayz_test_modes, inbox  # noqa: E402
from dayz_mcp.server import ServerConfig, build_app  # noqa: E402

# 1x1 white PNG, valid bytes: the server base64-decodes with validate=True.
_PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="
)

# Preservation line: `required` of every touched tool, frozen as literals (never read from the app).
FROZEN_REQUIRED = {
    "pipeline_resolve": ["feedback_id", "resolution"],
    "capture_screenshot": [],
    "ui_click": ["path"],
    "ui_reload_layout": [],
    "dayz_knowledge_status": [],
    "dayz_knowledge_prepare": [],
    "dayz_test_run": ["project", "mode"],
}
TOUCHED = list(FROZEN_REQUIRED)


def _blocks(result):
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, dict):
        return [result]
    return list(result)


def _text_json(result):
    blocks = _blocks(result)
    if blocks and isinstance(blocks[0], dict):
        return blocks[0]
    return json.loads(blocks[0].text)


def _schema(app, name):
    tool = app._tool_manager.get_tool(name)
    return tool.parameters if tool is not None else None


def _desc(app, name):
    tool = app._tool_manager.get_tool(name)
    return (tool.description or "") if tool is not None else ""


def _maxlen(prop: dict) -> int | None:
    if "maxLength" in prop:
        return prop["maxLength"]
    for alt in prop.get("anyOf", []):
        if isinstance(alt, dict) and "maxLength" in alt:
            return alt["maxLength"]
    return None


async def _err(coro) -> str | None:
    try:
        await coro
    except Exception as exc:  # noqa: BLE001
        return str(exc)
    return None


async def main() -> int:
    app, runtime = build_app(ServerConfig(key="oracle-key", port=0, log_sink=lambda _m: None))
    tools = {t.name: t for t in app._tool_manager.list_tools()}

    # ---------------------------------------------------------------- preservation line
    check("P0-count", len(tools) == 60, f"tools={len(tools)}")
    for name, req in FROZEN_REQUIRED.items():
        sch = _schema(app, name)
        if sch is None:
            unmet(f"P0-required-{name}", "tool missing")
            continue
        check(f"P0-required-{name}", sorted(sch.get("required", []) or []) == sorted(req),
              f"required={sch.get('required')}")
    check("P0-scene_raycast-alias", "from" in (_schema(app, "scene_raycast") or {}).get("properties", {}),
          "the alias patch must survive whatever helper generalises it")

    # additionalProperties:false on every touched tool (ea10 decision) + extras rejected
    for name in TOUCHED:
        sch = _schema(app, name) or {}
        check(f"P0-addprops-{name}", sch.get("additionalProperties") is False,
              f"additionalProperties={sch.get('additionalProperties')!r}")

    # ---------------------------------------------------------------- P1 pipeline_resolve
    sch = _schema(app, "pipeline_resolve") or {}
    prop = sch.get("properties", {}).get("evidence_ref")
    check("P1-A-schema", isinstance(prop, dict) and _maxlen(prop) == 240
          and "evidence_ref" not in (sch.get("required") or []),
          f"evidence_ref={prop!r}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp) / "inbox"
        with patch.object(inbox, "INBOX_DIR", tmp_dir), \
             patch.object(inbox, "FEEDBACK_PATH", tmp_dir / "feedback.jsonl"):
            created = _text_json(await app.call_tool("pipeline_feedback", {
                "kind": "bug", "title": "oracle-m entry", "body": "tool: x\nargs: {}\nerror: e\nrepro: r"}))
            fid = created.get("id")
            if not isinstance(fid, str):
                unmet("P1-B-roundtrip", f"pipeline_feedback returned {created!r}")
            else:
                # (D) preservation: two-argument call still works
                err = await _err(app.call_tool("pipeline_resolve", {"feedback_id": fid, "resolution": "first"}))
                check("P1-D-legacy-two-args", err is None, err or "")
                # (B) the pointer travels: resolve with evidence_ref, read back through pipeline_inbox
                err = await _err(app.call_tool("pipeline_resolve", {
                    "feedback_id": fid, "resolution": "second, with pointer",
                    "evidence_ref": "reviews/2026-09-04-lote-M/REVIEW.md"}))
                listed = _text_json(await app.call_tool("pipeline_inbox", {"include_resolved": True, "limit": 50}))
                entry = next((e for e in listed.get("entries", []) if e.get("id") == fid), None)
                check("P1-B-roundtrip", err is None and entry is not None
                      and entry.get("evidence_ref") == "reviews/2026-09-04-lote-M/REVIEW.md"
                      and entry.get("resolution") == "second, with pointer",
                      f"err={err!r} entry={entry!r}")
                raw = (tmp_dir / "feedback.jsonl").read_text(encoding="utf-8") if (tmp_dir / "feedback.jsonl").is_file() else ""
                check("P1-B2-persisted", '"evidence_ref": "reviews/2026-09-04-lote-M/REVIEW.md"' in raw
                      or '"evidence_ref":"reviews/2026-09-04-lote-M/REVIEW.md"' in raw,
                      "the ref must be in the JSONL, not only in the listing")
                # (C) the helper's validator is reachable: an absolute path is rejected, naming the field
                err = await _err(app.call_tool("pipeline_resolve", {
                    "feedback_id": fid, "resolution": "third", "evidence_ref": "C:\\abs\\path.md"}))
                check("P1-C-invalid-ref-rejected", err is not None and "evidence_ref" in err, f"err={err!r}")
                # (C2) too long -> rejected before the helper (schema) or by it; either way named
                err = await _err(app.call_tool("pipeline_resolve", {
                    "feedback_id": fid, "resolution": "fourth", "evidence_ref": "reviews/" + "x" * 240}))
                check("P1-C2-too-long-rejected", err is not None and "evidence_ref" in err, f"err={err!r}")
                # (E) unknown argument is a hard error, not a silent drop
                err = await _err(app.call_tool("pipeline_resolve", {
                    "feedback_id": fid, "resolution": "fifth", "bogus": 1}))
                check("P1-E-extras-rejected", err is not None and "bad_args: unexpected arguments" in err, f"err={err!r}")

    # ---------------------------------------------------------------- P2 knowledge tools
    for name in ("dayz_knowledge_status", "dayz_knowledge_prepare"):
        sch = _schema(app, name) or {}
        check(f"P2-A-schema-{name}", sch.get("properties") == {} and sch.get("required") == []
              and sch.get("additionalProperties") is False, f"schema={sch!r}")
    err = await _err(app.call_tool("dayz_knowledge_status", {"repo": "Y"}))
    check("P2-B-inprocess-extras", err is not None and "bad_args: unexpected arguments" in err, f"err={err!r}")
    err = await _err(app.call_tool("dayz_knowledge_status", {}))
    check("P2-B2-inprocess-empty-ok", err is None, err or "")
    try:
        from mcp.shared.memory import create_connected_server_and_client_session
        async with create_connected_server_and_client_session(app._mcp_server) as session:
            res = await session.call_tool("dayz_knowledge_prepare", {"path": "X"})
            txt = " ".join(getattr(c, "text", "") for c in res.content)
            check("P2-C-wire-extras-prepare", res.isError is True and "unexpected arguments" in txt,
                  f"isError={res.isError} text={txt[:120]!r}")
            res = await session.call_tool("dayz_knowledge_status", {"repo": "Y"})
            txt = " ".join(getattr(c, "text", "") for c in res.content)
            check("P2-C-wire-extras-status", res.isError is True and "unexpected arguments" in txt,
                  f"isError={res.isError} text={txt[:120]!r}")
            res = await session.call_tool("dayz_knowledge_status", {})
            check("P2-C2-wire-empty-ok", res.isError is False, f"isError={res.isError}")
            # the same wire path for the other touched tools
            res = await session.call_tool("ui_click", {"path": "a/b", "wiggle": 1, "timeout_s": 0.5})
            txt = " ".join(getattr(c, "text", "") for c in res.content)
            check("P2-C-wire-extras-ui_click", res.isError is True and "unexpected arguments" in txt,
                  f"isError={res.isError} text={txt[:120]!r}")
    except Exception as exc:  # noqa: BLE001
        unmet("P2-C-wire", f"client session unavailable: {exc!r}")

    # ---------------------------------------------------------------- P3 capture_screenshot
    sch = _schema(app, "capture_screenshot") or {}
    prop = sch.get("properties", {}).get("crop_space")
    check("P3-A-schema", isinstance(prop, dict) and prop.get("default") == mcp_capture.DEFAULT_CROP_SPACE
          and "crop_space" not in (sch.get("required") or []), f"crop_space={prop!r}")

    captured: list[dict] = []

    def _fake_capture(**kwargs):
        captured.append(dict(kwargs))
        meta = {
            "native_width": 4, "native_height": 4, "crop": kwargs.get("crop", ""),
            "crop_space": kwargs.get("crop_space", "<absent>"),
            "window": {"pid": 1, "class": "c", "title": "t", "left": 0, "top": 0, "width": 4, "height": 4},
            "frame_sha256": "f" * 64,
            "window_surface": {"rect": {}, "pixel_sha256": "f" * 64, "stats": {}},
            "client_surface": None,
            "effective_surface": {"rect_window": {}, "native_width": 4, "native_height": 4},
            "fullres_file_sha256": None,
        }
        out = {"inline": {"data": base64.b64encode(_PNG_1x1).decode("ascii"), "mimeType": "image/png"},
               "fullres_path": None, "meta": meta}
        if kwargs.get("save_fullres"):
            out["fullres_path"] = "C:/fake/fullres.jpg"
            meta["fullres_file_sha256"] = "a" * 64
        return out

    runtime.lifecycle_status = AsyncMock(return_value={"runs": []})
    from dayz_mcp import server as server_mod
    with patch.object(server_mod.mcp_capture, "capture_dual", side_effect=_fake_capture):
        captured.clear()
        err = await _err(app.call_tool("capture_screenshot", {}))
        got = captured[-1] if captured else {}
        check("P3-B-forward-default", err is None and got.get("crop_space") == mcp_capture.DEFAULT_CROP_SPACE,
              f"err={err!r} kwargs_crop_space={got.get('crop_space', '<absent>')!r}")
        captured.clear()
        err = await _err(app.call_tool("capture_screenshot", {"crop_space": "window"}))
        got = captured[-1] if captured else {}
        check("P3-B2-forward-explicit", err is None and got.get("crop_space") == "window",
              f"err={err!r} kwargs_crop_space={got.get('crop_space', '<absent>')!r}")
        # return shape: [image, json-meta] in BOTH save_fullres arms
        for arm in (False, True):
            try:
                res = _blocks(await app.call_tool("capture_screenshot", {"save_fullres": arm}))
            except Exception as exc:  # noqa: BLE001
                check(f"P3-C-shape-fullres={arm}", False, f"raised {exc!r}")
                continue
            kinds = [type(b).__name__ for b in res]
            meta = None
            for b in res:
                if getattr(b, "type", "") == "text":
                    try:
                        meta = json.loads(b.text)
                    except Exception:  # noqa: BLE001
                        meta = None
            ok = (len(res) == 2 and getattr(res[0], "type", "") == "image" and isinstance(meta, dict)
                  and all(k in meta for k in ("crop_space", "window_surface", "client_surface", "effective_surface", "frame_sha256"))
                  and (not arm or meta.get("fullres_path") == "C:/fake/fullres.jpg"))
            check(f"P3-C-shape-fullres={arm}", ok, f"kinds={kinds} meta_keys={sorted(meta) if isinstance(meta, dict) else meta}")
    # bad crop_space: the REAL helper rejects before any grab; the token must reach the caller
    err = await _err(app.call_tool("capture_screenshot", {"crop_space": "bogus"}))
    check("P3-D-bad_crop_space", err is not None and "bad_crop_space" in err, f"err={err!r}")
    err = await _err(app.call_tool("capture_screenshot", {"bogus": 1}))
    check("P3-E-extras-rejected", err is not None and "bad_args: unexpected arguments" in err, f"err={err!r}")

    # ---------------------------------------------------------------- P4 ui_click / ui_reload_layout
    sch = _schema(app, "ui_click") or {}
    mode = sch.get("properties", {}).get("mode", {})
    check("P4-A-ui_click-mode-enum", mode.get("enum") == ["direct", "complete"], f"mode={mode!r}")
    sch2 = _schema(app, "ui_reload_layout") or {}
    mode2 = sch2.get("properties", {}).get("mode", {})
    check("P4-A2-ui_reload_layout-mode-enum", mode2.get("enum") == ["reload", "close"], f"mode={mode2!r}")
    err = await _err(app.call_tool("ui_click", {"path": "a/b", "mode": "", "timeout_s": 0.5}))
    check("P4-B-empty-mode-rejected", err is not None and "mode" in err, f"err={err!r}")
    for bad in (True, "1", 1.0):
        err = await _err(app.call_tool("ui_click", {"path": "a/b", "button": bad, "timeout_s": 0.5}))
        check(f"P4-C-button-strict-{bad!r}", err is not None and "button" in err, f"err={err!r}")
    err = await _err(app.call_tool("ui_click", {"path": "a/b", "button": 3, "timeout_s": 0.5}))
    check("P4-C2-button-range-kept", err is not None and "button" in err, f"err={err!r}")
    check("P4-D-desc-mode_not_implemented", "mode_not_implemented" in _desc(app, "ui_click"),
          "ui_click description must name the bridge's rejection code for mode=complete")
    err = await _err(app.call_tool("ui_click", {"path": "a/b", "wiggle": 1, "timeout_s": 0.5}))
    check("P4-E-extras-rejected", err is not None and "bad_args: unexpected arguments" in err, f"err={err!r}")

    # ---------------------------------------------------------------- P5 dayz_test_run
    sch = _schema(app, "dayz_test_run") or {}
    mode = sch.get("properties", {}).get("mode", {})
    enum = mode.get("enum")
    authority = set(dayz_test_modes.public_mode_names())
    check("P5-A-mode-enum-is-authority", isinstance(enum, list) and set(enum) == authority and "offline" not in enum,
          f"enum={enum!r} authority={sorted(authority)}")
    desc = _desc(app, "dayz_test_run")
    check("P5-B-desc-extra_mods", "extra_mods" in desc and "mod_roots" in desc,
          "description must say extra_mods accepts any folder under the project's mod_roots")
    err = await _err(app.call_tool("dayz_test_run", {"project": "DayZ_MCP", "mode": "offline"}))
    check("P5-C-offline-rejected", err is not None and "mode" in err, f"err={err!r}")

    # ---------------------------------------------------------------- verdict
    counts = {"PASS": 0, "FAIL": 0, "UNMET": 0}
    for name, status, detail in RESULTS:
        counts[status] += 1
        print(f"{status:5} {name}" + (f"  -- {detail}" if detail and status != "PASS" else ""))
    print(f"PASS={counts['PASS']}  FAIL={counts['FAIL']}  UNMET={counts['UNMET']}  de {len(RESULTS)}")
    if counts["FAIL"] == 0 and counts["UNMET"] == 0:
        print("ORACULO-VERDE")
        return 0
    print("ORACULO-ROJO")
    return 1


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    sys.exit(asyncio.run(main()))

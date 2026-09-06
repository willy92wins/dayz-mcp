"""Lote M wire-surface tests: one positive and one negative per product."""

from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from dayz_mcp import dayz_test_modes, inbox, server
from dayz_mcp.server import ServerConfig, build_app

_PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="
)


def _text_json(result):
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, dict):
        return result
    return json.loads(result[0].text)


class LoteMProductsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app, self.runtime = build_app(
            ServerConfig(key="k", port=0, log_sink=lambda _m: None)
        )

    async def test_p0_closed_schema_rejects_extras_inprocess(self) -> None:
        with self.assertRaises(Exception) as ctx:
            await self.app.call_tool("pipeline_resolve", {
                "feedback_id": "x", "resolution": "y", "bogus": 1,
            })
        self.assertIn("bad_args: unexpected arguments", str(ctx.exception))

    async def test_p0_closed_schema_rejects_extras_wire(self) -> None:
        from mcp.shared.memory import create_connected_server_and_client_session

        async with create_connected_server_and_client_session(self.app._mcp_server) as session:
            res = await session.call_tool("dayz_knowledge_status", {"repo": "Y"})
            txt = " ".join(getattr(c, "text", "") for c in res.content)
            self.assertTrue(res.isError)
            self.assertIn("unexpected arguments", txt)

    async def test_p1_evidence_ref_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp) / "inbox"
            with patch.object(inbox, "INBOX_DIR", tmp_dir), \
                 patch.object(inbox, "FEEDBACK_PATH", tmp_dir / "feedback.jsonl"):
                created = _text_json(await self.app.call_tool("pipeline_feedback", {
                    "kind": "bug", "title": "t", "body": "tool: x\nargs: {}\nerror: e\nrepro: r",
                }))
                fid = created["id"]
                await self.app.call_tool("pipeline_resolve", {
                    "feedback_id": fid,
                    "resolution": "done",
                    "evidence_ref": "reviews/2026-09-04-lote-M/REVIEW.md",
                })
                listed = _text_json(await self.app.call_tool("pipeline_inbox", {
                    "include_resolved": True, "limit": 50,
                }))
                entry = next(e for e in listed["entries"] if e["id"] == fid)
                self.assertEqual(entry["evidence_ref"], "reviews/2026-09-04-lote-M/REVIEW.md")

    async def test_p1_invalid_evidence_ref_rejected(self) -> None:
        with self.assertRaises(Exception) as ctx:
            await self.app.call_tool("pipeline_resolve", {
                "feedback_id": "fb-x", "resolution": "nope",
                "evidence_ref": "C:\\abs\\path.md",
            })
        self.assertIn("evidence_ref", str(ctx.exception))

    async def test_p2_knowledge_empty_args_ok(self) -> None:
        err = None
        try:
            await self.app.call_tool("dayz_knowledge_status", {})
        except Exception as exc:
            err = exc
        self.assertIsNone(err)

    async def test_p2_knowledge_extras_rejected(self) -> None:
        with self.assertRaises(Exception) as ctx:
            await self.app.call_tool("dayz_knowledge_prepare", {"path": "X"})
        self.assertIn("bad_args: unexpected arguments", str(ctx.exception))

    async def test_p3_crop_space_forwarded(self) -> None:
        captured: list[dict] = []

        def _fake(**kwargs):
            captured.append(dict(kwargs))
            return {
                "inline": {
                    "data": base64.b64encode(_PNG_1x1).decode("ascii"),
                    "mimeType": "image/png",
                },
                "fullres_path": None,
                "meta": {
                    "crop_space": kwargs.get("crop_space"),
                    "frame_sha256": "f" * 64,
                    "window_surface": {},
                    "client_surface": None,
                    "effective_surface": {},
                },
            }

        self.runtime.lifecycle_status = AsyncMock(return_value={"runs": []})
        with patch.object(server.mcp_capture, "capture_dual", side_effect=_fake):
            blocks = await self.app.call_tool("capture_screenshot", {"crop_space": "window"})
        self.assertEqual(captured[-1]["crop_space"], "window")
        self.assertEqual(len(blocks), 2)

    async def test_p3_bad_crop_space_rejected(self) -> None:
        self.runtime.lifecycle_status = AsyncMock(return_value={"runs": []})
        with self.assertRaises(Exception) as ctx:
            await self.app.call_tool("capture_screenshot", {"crop_space": "bogus"})
        self.assertIn("bad_crop_space", str(ctx.exception))

    async def test_p3_public_round_trip_returns_image_and_meta_in_both_fullres_arms(self) -> None:
        # Plan sheet 03 (:41): the JSON block travels in the default route too, not only
        # when a full-resolution file is written. Both arms through the public tool.
        def _fake(**kwargs):
            out = {
                "inline": {
                    "data": base64.b64encode(_PNG_1x1).decode("ascii"),
                    "mimeType": "image/png",
                },
                "fullres_path": None,
                "meta": {
                    "crop_space": kwargs.get("crop_space"),
                    "frame_sha256": "f" * 64,
                    "window_surface": {"pixel_sha256": "f" * 64},
                    "client_surface": None,
                    "effective_surface": {},
                    "fullres_file_sha256": None,
                },
            }
            if kwargs.get("save_fullres"):
                out["fullres_path"] = "C:/fake/fullres.jpg"
                out["meta"]["fullres_file_sha256"] = "a" * 64
            return out

        self.runtime.lifecycle_status = AsyncMock(return_value={"runs": []})
        for arm in (False, True):
            with self.subTest(save_fullres=arm):
                with patch.object(server.mcp_capture, "capture_dual", side_effect=_fake):
                    blocks = await self.app.call_tool("capture_screenshot", {"save_fullres": arm})
                if isinstance(blocks, tuple):
                    blocks = blocks[0]
                self.assertEqual(2, len(blocks))
                self.assertEqual("image", blocks[0].type)
                meta = json.loads(blocks[1].text)
                for key in ("crop_space", "window_surface", "client_surface", "effective_surface", "frame_sha256", "fullres_path"):
                    self.assertIn(key, meta)
                self.assertEqual(server.mcp_capture.DEFAULT_CROP_SPACE, meta["crop_space"])
                self.assertEqual("C:/fake/fullres.jpg" if arm else None, meta["fullres_path"])

    async def test_p4_ui_click_mode_enum_and_strict_button(self) -> None:
        tool = self.app._tool_manager.get_tool("ui_click")
        mode = tool.parameters["properties"]["mode"]
        self.assertEqual(mode.get("enum"), ["direct", "complete"])
        with self.assertRaises(Exception) as ctx:
            await self.app.call_tool("ui_click", {"path": "a/b", "button": True, "timeout_s": 0.5})
        self.assertIn("button", str(ctx.exception))

    async def test_p4_ui_reload_layout_mode_enum(self) -> None:
        tool = self.app._tool_manager.get_tool("ui_reload_layout")
        mode = tool.parameters["properties"]["mode"]
        self.assertEqual(mode.get("enum"), ["reload", "close"])

    async def test_p5_mode_enum_from_authority(self) -> None:
        tool = self.app._tool_manager.get_tool("dayz_test_run")
        enum = tool.parameters["properties"]["mode"]["enum"]
        self.assertEqual(set(enum), set(dayz_test_modes.public_mode_names()))
        self.assertNotIn("offline", enum)

    async def test_p5_enum_follows_a_substituted_authority_in_a_new_app(self) -> None:
        # Codex B-01 (2026-09-04): the enum must be read from the authority when the app is
        # built and on every call, never frozen at import of server.py.
        from dataclasses import replace

        original = dayz_test_modes.MODE_RECORDS
        offline = next(record for record in original if record.name == "offline")
        try:
            dayz_test_modes.MODE_RECORDS = (replace(offline, public=True, default_when_omitted=True),)
            app, _runtime = build_app(ServerConfig(key="k", port=0, log_sink=lambda _m: None))
            enum = app._tool_manager.get_tool("dayz_test_run").parameters["properties"]["mode"]["enum"]
            self.assertEqual(list(dayz_test_modes.public_mode_names()), enum)
            self.assertEqual(["offline"], enum)
            with self.assertRaises(Exception) as ctx:
                await app.call_tool("dayz_test_run", {"project": "ExampleMod", "mode": "server"})
            self.assertIn("mode", str(ctx.exception))
        finally:
            dayz_test_modes.MODE_RECORDS = original

    async def test_p5_offline_rejected(self) -> None:
        with self.assertRaises(Exception) as ctx:
            await self.app.call_tool("dayz_test_run", {"project": "DayZ_MCP", "mode": "offline"})
        self.assertIn("mode", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

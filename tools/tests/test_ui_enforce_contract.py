"""Source contract of the client UI verbs in MCPClientBridge.c (M05-UI-ENFORCE).

These gates are structural: they read the Enforce source and check the shape
of the resolver, the request echo, the click error semantics and the canonical
`matched_path` builder, then re-run the same checks over mutated copies and
require red. Nothing here executes Enforce or proves an in-game effect; the
live gate (LFPG TEST fixture, receipts, widget/UID/colour pre- and post-images)
belongs to M25 and is not inherited from a green run of this module.
"""
from __future__ import annotations

import re
import unittest

from tests._addon_paths import addon_root
from tests.test_vehicle_telemetry_contract import (
    TELEMETRY_REGION_SHA256,
    _telemetry_sha256,
)


BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"
MESSAGES_PATH = addon_root() / "scripts" / "5_Mission" / "MCPMessages.c"

CORE_UI_VERBS = {
    "ui_tree": "protected bool DispatchUiTree(",
    "ui_set_text": "protected bool DispatchUiSetText(",
    "ui_click": "protected bool DispatchUiClick(",
    "ui_focus": "protected bool DispatchUiFocus(",
}
RELOAD_SIGNATURE = "protected bool DispatchUiReloadLayout("

# RFC3986 unreserved: ALPHA / DIGIT / "-" / "." / "_" / "~"
_UNRESERVED = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)


def _method_body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError(f"unterminated method: {signature}")


def _strip_comments(text: str) -> str:
    return re.sub(r"//[^\n]*", "", text)


def _compact(text: str) -> str:
    return " ".join(_strip_comments(text).split())


def _ordered(haystack: str, *needles: str) -> None:
    """Each needle must occur after the previous one (sequential search)."""
    last = -1
    for needle in needles:
        index = haystack.find(needle, last + 1)
        if index < 0:
            raise AssertionError(f"missing or out of order: {needle!r}")
        last = index


def _mutate(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise AssertionError(f"mutant anchor must be unique: {old!r}")
    return source.replace(old, new, 1)


# --- Reference model of the canonical matched_path (DAG v6) ------------------
#
# Only documents the rule the Enforce builder implements; it is not the producer
# and it does not accredit the Enforce bytes. The expected strings in the tests
# below are hand-written literals, not outputs of this model.


def percent_encode_segment(name: str) -> str:
    out = []
    for byte in name.encode("utf-8"):
        if byte in _UNRESERVED:
            out.append(chr(byte))
        else:
            out.append(f"%{byte:02X}")
    return "".join(out)


def canonical_path(chain: list[tuple[str, list[str]]]) -> str:
    """chain: root->leaf list of (name, sibling_names_in_order_including_self).

    The first entry is the workspace and always takes ordinal 0. For every
    other node the ordinal is the 0-based count of earlier siblings with the
    byte-identical name; `sibling_names` carries the target's own position as
    the last occurrence that matters, which is why callers pass the prefix up
    to and including the node itself.
    """
    if not chain:
        raise ValueError("chain must start at the workspace")
    segments = []
    workspace_name, _ = chain[0]
    segments.append(f"/{percent_encode_segment(workspace_name)}@0")
    for name, siblings_up_to_self in chain[1:]:
        if not siblings_up_to_self or siblings_up_to_self[-1] != name:
            raise ValueError("siblings must end at the node itself")
        ordinal = sum(1 for sibling in siblings_up_to_self[:-1] if sibling == name)
        segments.append(f"/{percent_encode_segment(name)}@{ordinal}")
    return "".join(segments)


# --- Verifiers ---------------------------------------------------------------


def verify_click_error_semantics(source: str) -> None:
    body = _method_body(source, CORE_UI_VERBS["ui_click"])
    compact = _compact(body)
    invoke = "bool didClick = InvokeUiClick(target, mouseButton, handlerName);"
    _ordered(
        compact,
        invoke,
        "result.user_id = target.GetUserID();",
        "result.handler = handlerName;",
        "result.clicked = didClick;",
        "if (!didClick) { result.ok = false; "
        'if (handlerName == "") { result.error = "no_handler"; } '
        'else { result.error = "not_handled"; } return true; }',
        "result.ok = true;",
    )
    if body.count('"no_handler"') != 1 or body.count('"not_handled"') != 1:
        raise AssertionError("no_handler / not_handled must each be assigned exactly once")
    if body.count("InvokeUiClick(") != 1:
        raise AssertionError("direct mode invokes the handler lookup exactly once")


def verify_resolver_contract(source: str) -> None:
    if "FindAnyWidget(" in source or "FindWidgetByNameWalk" in source:
        raise AssertionError("first-match lookup API still present")

    resolver = _method_body(source, "protected Widget ResolveUiRoot(MCPArgs args, out string error)")
    _ordered(
        resolver,
        'error = "no_game";',
        "WorkspaceWidget workspace = GetGame().GetWorkspace();",
        'error = "no_workspace";',
        "rootName = args.root;",
        "pathName = args.path;",
        "Widget scope = workspace;",
        'if (rootName != "")',
        "scope = ResolveUniqueUiWidget(workspace, rootName, rootError);",
        "error = rootError;",
        'if (pathName == "")',
        "return scope;",
        'if (pathName != "")',
        "Widget named = ResolveUniqueUiWidget(scope, pathName, pathError);",
        "error = pathError;",
        "return named;",
        "UIManager ui = GetGame().GetUIManager();",
        "UIScriptedMenu menu = ui.GetMenu();",
        "Widget menuRoot = menu.GetLayoutRoot();",
        'error = "no_menu";',
    )
    if resolver.count("GetMenu()") != 1:
        raise AssertionError("active-menu legacy must be a single fallback after the named paths")

    unique = _method_body(
        source, "protected Widget ResolveUniqueUiWidget(Widget scope, string name, out string error)"
    )
    # Each failing branch must CUT. Assigning the error and falling through to
    # `return match.first` would hand back the first homonym, which is exactly
    # the behaviour the counting resolver replaced.
    _ordered(
        _compact(unique),
        "MCPUiNameMatch match = new MCPUiNameMatch();",
        "CountUiWidgetsNamed(scope, name, match);",
        'if (match.count == 0) { error = "widget_not_found"; return null; }',
        'if (match.count > 1) { error = "ambiguous_path"; return null; }',
        'error = ""; return match.first;',
    )
    if unique.count("return match.first;") != 1 or unique.count('"ambiguous_path"') != 1:
        raise AssertionError("unique resolver must return the match only after both count checks")
    if unique.count("return null;") != 2:
        raise AssertionError("both failing branches must return null")

    count = _method_body(
        source, "protected void CountUiWidgetsNamed(Widget scope, string name, MCPUiNameMatch match)"
    )
    _ordered(
        count,
        "if (match.count >= 2)",
        "if (scope.GetName() == name)",
        "match.count = match.count + 1;",
        "if (match.count == 1)",
        "match.first = scope;",
        "Widget child = scope.GetChildren();",
        "CountUiWidgetsNamed(child, name, match);",
        "child = child.GetSibling();",
    )
    if "match.count >= 1" in count:
        raise AssertionError("walk must not stop at the first match")
    if "return scope;" in count or "return child;" in count:
        raise AssertionError("count walk must not return a widget")

    scratch = _method_body(source, "class MCPUiNameMatch")
    for member in ("int count;", "Widget first;", "count = 0;", "first = null;"):
        if member not in scratch:
            raise AssertionError(f"MCPUiNameMatch missing {member!r}")


def verify_matched_path_builder(source: str) -> None:
    build = _method_body(
        source, "protected string BuildUiMatchedPath(Widget target, WorkspaceWidget workspace)"
    )
    _ordered(
        build,
        "Widget cursor = target;",
        "while (cursor && cursor != workspace)",
        "int ordinal = UiSiblingOrdinal(cursor, workspace);",
        "if (ordinal < 0)",
        'return "";',
        "if (!EncodeUiPathSegment(cursor.GetName(), segment))",
        'path = "/" + segment + "@" + ordinal.ToString() + path;',
        "cursor = cursor.GetParent();",
        "if (!EncodeUiPathSegment(workspace.GetName(), segment))",
        'return "/" + segment + "@0" + path;',
    )
    if build.count('"@0"') != 1:
        raise AssertionError("workspace segment must be emitted exactly once at @0")

    ordinal = _method_body(
        source, "protected int UiSiblingOrdinal(Widget w, WorkspaceWidget workspace)"
    )
    _ordered(
        ordinal,
        "Widget parent = w.GetParent();",
        "parent = workspace;",
        "string name = w.GetName();",
        "int ordinal = 0;",
        "Widget sibling = parent.GetChildren();",
        "if (sibling == w)",
        "return ordinal;",
        "if (sibling.GetName() == name)",
        "ordinal = ordinal + 1;",
        "sibling = sibling.GetSibling();",
        "return -1;",
    )

    encoder = _method_body(
        source, "protected bool EncodeUiPathSegment(string value, out string encoded)"
    )
    _ordered(
        encoder,
        'string hexDigits = "0123456789ABCDEF";',
        "character = value.Substring(i, 1);",
        "code = character.ToAscii();",
        "if (code < 0)",
        "code = code + 256;",
        "if (code < 0 || code > 255)",
        "return false;",
        "if (IsUiPathUnreserved(code))",
        "encoded = encoded + character;",
        'encoded = encoded + "%" + hexDigits.Substring(highNibble, 1) + hexDigits.Substring(lowNibble, 1);',
        "return true;",
    )
    if "abcdef" in encoder:
        raise AssertionError("percent-encoding hex must be uppercase")

    unreserved = _method_body(source, "protected bool IsUiPathUnreserved(int code)")
    for needle in (
        "code >= 65 && code <= 90",
        "code >= 97 && code <= 122",
        "code >= 48 && code <= 57",
        "code == 45 || code == 46 || code == 95 || code == 126",
    ):
        if needle not in unreserved:
            raise AssertionError(f"unreserved set missing {needle!r}")
    for reserved in ("code == 37", "code == 47", "code == 64"):
        if reserved in unreserved:
            raise AssertionError("'%', '/' and '@' must always be encoded")

    fill = _method_body(source, "protected void FillUiMatchedPath(Widget target, MCPResult result)")
    if "result.ui_request.matched_path = BuildUiMatchedPath(target, workspace);" not in fill:
        raise AssertionError("matched_path must come from the live widget builder")
    if source.count("matched_path =") != 1:
        raise AssertionError("matched_path is assigned only from the builder, never copied")


def verify_ui_request_echo(source: str) -> None:
    begin = _method_body(source, "protected void BeginUiRequest(MCPArgs args, MCPResult result)")
    _ordered(
        begin,
        "result.ui_request = new MCPUiRequestEcho();",
        "result.ui_request.requested_path = args.path;",
        "result.ui_request.requested_root = args.root;",
    )
    if "matched_path" in begin or "requested_text" in begin:
        raise AssertionError("BeginUiRequest copies only path and root")

    bodies = {verb: _method_body(source, sig) for verb, sig in CORE_UI_VERBS.items()}
    for verb, body in bodies.items():
        begin_call = "BeginUiRequest(args, result);" if verb == "ui_tree" else "BeginUiRequest(command.args, result);"
        begin_idx = body.find(begin_call)
        if begin_idx < 0:
            raise AssertionError(f"{verb}: request echo not instantiated")
        first_return = body.find("return true;")
        resolve_idx = body.find("ResolveUiRoot(")
        if first_return < 0 or resolve_idx < 0:
            raise AssertionError(f"{verb}: dispatch shape changed")
        if begin_idx > first_return or begin_idx > resolve_idx:
            raise AssertionError(f"{verb}: echo must exist before any return and before resolving")
        fill_idx = body.find("FillUiMatchedPath(")
        if fill_idx < 0 or fill_idx < resolve_idx:
            raise AssertionError(f"{verb}: matched_path must be filled only after resolution")
        if "result.source" in body:
            raise AssertionError(f"{verb}: source must carry no UI meaning")
        if verb != "ui_set_text" and "requested_text" in body:
            raise AssertionError(f"{verb}: requested_text belongs to set-text only")

    set_text = bodies["ui_set_text"]
    _ordered(
        set_text,
        "BeginUiRequest(command.args, result);",
        "result.ui_request.requested_text = command.args.text;",
        'result.error = "bad_args";',
        "ResolveUiRoot(",
        "FillUiMatchedPath(target, result);",
        'result.error = "text_not_writable";',
    )

    tree = bodies["ui_tree"]
    _ordered(
        tree,
        "ResolveUiRoot(",
        "if (UiRequestNamesTarget(args))",
        "FillUiMatchedPath(root, result);",
        "CollectUiNodes(root, snap, limit);",
    )
    names_target = _method_body(source, "protected bool UiRequestNamesTarget(MCPArgs args)")
    if 'args.root != ""' not in names_target or 'return args.path != "";' not in names_target:
        raise AssertionError("ui_tree must skip matched_path only for the active-menu legacy")

    focus = bodies["ui_focus"]
    _ordered(
        focus,
        "BeginUiRequest(command.args, result);",
        "ResolveUiRoot(",
        "result.found = true;",
        "FillUiMatchedPath(target, result);",
        "activeOk = SetActiveWindow(topmost, false);",
        "SetFocus(target);",
        "focused = GetFocus();",
        "result.ok = (focused == target);",
        'result.error = "focus_not_taken";',
    )
    if "focused.GetName()" in focus or "focused.GetParent()" in focus:
        raise AssertionError("focus holder identity must stay a local handle")

    click = bodies["ui_click"]
    _ordered(
        click,
        "BeginUiRequest(command.args, result);",
        "ResolveUiRoot(",
        "if (!target)",
        "FillUiMatchedPath(target, result);",
        "InvokeUiClick(",
    )

    reload = _method_body(source, RELOAD_SIGNATURE)
    _ordered(
        reload,
        'bool closing = (args.mode == "close");',
        "if (closing)",
        "result.ui_request = new MCPUiRequestEcho();",
        'result.ui_request.requested_path = "";',
        'Log("ui_reload_layout closed preview");',
        "FileExist(args.path)",
        "CreateWidgets(args.path)",
        "CollectUiNodes(m_UiPreviewRoot, snap, limit);",
        "result.ui_request.requested_path = args.path;",
    )
    if reload.count("result.ui_request = new MCPUiRequestEcho();") != 2:
        raise AssertionError("reload must echo on both successes")
    if "result.source" in reload:
        raise AssertionError("reload: source must carry no UI meaning")


def verify_click_mode_contract(source: str) -> None:
    click = _method_body(source, CORE_UI_VERBS["ui_click"])
    if "bubble" in click:
        raise AssertionError("direct mode must not read bubble")
    if click.count("InvokeUiClick(") != 1:
        raise AssertionError("only the direct branch may reach the handler lookup")
    complete_idx = click.find('if (mode == "complete")')
    third_idx = click.find('if (mode != "direct")')
    if complete_idx < 0 or third_idx < 0 or third_idx < complete_idx:
        raise AssertionError("complete and unknown-mode gates must both exist, complete first")
    complete_block = click[complete_idx:third_idx]
    if (
        "return true;" not in complete_block
        or "InvokeUiClick(" in complete_block
        or re.search(r"\bmode = ", complete_block)
    ):
        raise AssertionError("complete must return before any handler runs, never rewrite the mode")
    _ordered(
        click,
        "ResolveUiRoot(",
        "if (!target)",
        "FillUiMatchedPath(target, result);",
        "string mode = command.args.mode;",
        'if (mode == "")',
        'mode = "direct";',
        'if (mode == "complete")',
        'result.error = "mode_not_implemented";',
        'if (mode != "direct")',
        'result.error = "bad_args";',
        "InvokeUiClick(",
    )


def verify_messages_echo_contract(messages: str) -> None:
    echo = _method_body(messages, "class MCPUiRequestEcho")
    for member in (
        "string requested_path;",
        "string requested_root;",
        "string requested_text;",
        "string matched_path;",
    ):
        if member not in echo:
            raise AssertionError(f"MCPUiRequestEcho missing {member!r}")
    result = _method_body(messages, "class MCPResult")
    if "ref MCPUiRequestEcho ui_request;" not in result:
        raise AssertionError("MCPResult must carry ref ui_request")
    args = _method_body(messages, "class MCPArgs")
    for member in ("string path;", "string root;", "bool bubble;", "string mode;"):
        if member not in args:
            raise AssertionError(f"MCPArgs missing {member!r}")


class UiEnforceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = BRIDGE_PATH.read_text(encoding="utf-8")
        cls.messages = MESSAGES_PATH.read_text(encoding="utf-8")

    def test_telemetry_region_owned_by_m04_is_untouched(self) -> None:
        self.assertEqual(_telemetry_sha256(self.source), TELEMETRY_REGION_SHA256)

    def test_messages_contract_from_m02_is_what_this_module_consumes(self) -> None:
        verify_messages_echo_contract(self.messages)

    def test_click_error_semantics(self) -> None:
        verify_click_error_semantics(self.source)

    def test_click_error_semantics_mutants_are_red(self) -> None:
        swapped = (
            self.source.replace('"no_handler"', "\0")
            .replace('"not_handled"', '"no_handler"')
            .replace("\0", '"not_handled"')
        )
        collapsed = self.source.replace('result.error = "not_handled";', 'result.error = "no_handler";')
        for label, mutant in (("swapped", swapped), ("collapsed", collapsed)):
            with self.subTest(mutant=label):
                self.assertNotEqual(mutant, self.source)
                with self.assertRaises(AssertionError):
                    verify_click_error_semantics(mutant)

    def test_resolver_contract(self) -> None:
        verify_resolver_contract(self.source)

    def test_resolver_mutants_are_red(self) -> None:
        source = self.source
        mutants = {
            "first_homonym_wins": _mutate(
                source,
                '\t\tif (match.count > 1)\n\t\t{\n\t\t\terror = "ambiguous_path";\n\t\t\treturn null;\n\t\t}\n',
                "",
            ),
            "engine_first_match": _mutate(
                source,
                "\t\tCountUiWidgetsNamed(scope, name, match);\n",
                "\t\tmatch.first = scope.FindAnyWidget(name);\n\t\tmatch.count = 1;\n",
            ),
            "walk_stops_at_first": _mutate(
                source,
                "\t\t\tmatch.count = match.count + 1;\n\t\t\tif (match.count == 1)\n\t\t\t{\n\t\t\t\tmatch.first = scope;\n\t\t\t}\n\t\t\tif (match.count >= 2)\n",
                "\t\t\tmatch.count = match.count + 1;\n\t\t\tif (match.count == 1)\n\t\t\t{\n\t\t\t\tmatch.first = scope;\n\t\t\t}\n\t\t\tif (match.count >= 1)\n",
            ),
            "scope_reduced_to_menu": _mutate(
                source,
                "\t\tWidget scope = workspace;\n",
                "\t\tWidget scope = GetGame().GetUIManager().GetMenu().GetLayoutRoot();\n",
            ),
            "root_ambiguity_silent": _mutate(
                source,
                "\t\t\tscope = ResolveUniqueUiWidget(workspace, rootName, rootError);\n",
                "\t\t\tscope = workspace.FindAnyWidget(rootName);\n",
            ),
            "root_truncates_scope_of_path": _mutate(
                source,
                "\t\t\tWidget named = ResolveUniqueUiWidget(scope, pathName, pathError);\n",
                "\t\t\tWidget named = ResolveUniqueUiWidget(workspace, pathName, pathError);\n",
            ),
            # Keeps the `if` and the error literal but drops only the cut: the
            # resolver would fall through to `return match.first` and click the
            # first homonym while reporting ambiguous_path.
            "ambiguous_branch_without_cut": _mutate(
                source,
                '\t\t\terror = "ambiguous_path";\n\t\t\treturn null;\n',
                '\t\t\terror = "ambiguous_path";\n',
            ),
            "not_found_branch_without_cut": _mutate(
                source,
                '\t\t\terror = "widget_not_found";\n\t\t\treturn null;\n',
                '\t\t\terror = "widget_not_found";\n',
            ),
        }
        for label, mutant in mutants.items():
            with self.subTest(mutant=label):
                with self.assertRaises(AssertionError):
                    verify_resolver_contract(mutant)
        for label in ("ambiguous_branch_without_cut", "not_found_branch_without_cut"):
            with self.subTest(mutant=label, reason="cut"):
                with self.assertRaisesRegex(AssertionError, "return null"):
                    verify_resolver_contract(mutants[label])

    def test_matched_path_builder(self) -> None:
        verify_matched_path_builder(self.source)

    def test_matched_path_builder_mutants_are_red(self) -> None:
        source = self.source
        mutants = {
            "lowercase_hex": _mutate(
                source,
                '\t\tstring hexDigits = "0123456789ABCDEF";\n\t\tstring character = "";\n\t\tint code = 0;',
                '\t\tstring hexDigits = "0123456789abcdef";\n\t\tstring character = "";\n\t\tint code = 0;',
            ),
            "ordinal_one_based": _mutate(source, "\t\tint ordinal = 0;\n", "\t\tint ordinal = 1;\n"),
            "workspace_segment_omitted": _mutate(
                source,
                '\t\treturn "/" + segment + "@0" + path;\n',
                "\t\treturn path;\n",
            ),
            "workspace_ordinal_not_zero": _mutate(
                source,
                '\t\treturn "/" + segment + "@0" + path;\n',
                '\t\treturn "/" + segment + "@1" + path;\n',
            ),
            "copied_from_request": _mutate(
                source,
                "\t\tresult.ui_request.matched_path = BuildUiMatchedPath(target, workspace);\n",
                "\t\tresult.ui_request.matched_path = result.ui_request.requested_path;\n",
            ),
            "at_sign_left_unencoded": _mutate(
                source,
                "\t\tif (code == 45 || code == 46 || code == 95 || code == 126)\n",
                "\t\tif (code == 45 || code == 46 || code == 95 || code == 126 || code == 64)\n",
            ),
            "guessed_ordinal_when_not_a_sibling": _mutate(
                source,
                "\t\t\tsibling = sibling.GetSibling();\n\t\t}\n\n\t\treturn -1;\n",
                "\t\t\tsibling = sibling.GetSibling();\n\t\t}\n\n\t\treturn ordinal;\n",
            ),
        }
        for label, mutant in mutants.items():
            with self.subTest(mutant=label):
                with self.assertRaises(AssertionError):
                    verify_matched_path_builder(mutant)

    def test_ui_request_echo(self) -> None:
        verify_ui_request_echo(self.source)

    def test_ui_request_echo_mutants_are_red(self) -> None:
        source = self.source
        mutants = {
            "focus_holder_name_in_source": _mutate(
                source,
                "\t\tresult.ok = (focused == target);\n",
                "\t\tresult.ok = (focused == target);\n\t\tif (focused)\n\t\t{\n\t\t\tresult.source = focused.GetName();\n\t\t}\n",
            ),
            "tree_without_echo": _mutate(source, "\t\tBeginUiRequest(args, result);\n", ""),
            "set_text_drops_empty_text_echo": _mutate(
                source,
                "\t\t\tresult.ui_request.requested_text = command.args.text;\n",
                "",
            ),
            "reload_close_echoes_request_path": _mutate(
                source,
                '\t\t\tresult.ui_request.requested_path = "";\n',
                "\t\t\tresult.ui_request.requested_path = args.path;\n",
            ),
            "reload_load_recovers_source": _mutate(
                source,
                "\t\tresult.ui_request.requested_path = args.path;\n\t\tresult.ok = true;\n",
                "\t\tresult.ui_request.requested_path = args.path;\n\t\tresult.source = args.path;\n\t\tresult.ok = true;\n",
            ),
            "click_echo_after_first_return": _mutate(
                source,
                "\t\tBeginUiRequest(command.args, result);\n\t\tif (!command.args || command.args.path == \"\")\n\t\t{\n\t\t\tresult.ok = false;\n\t\t\tresult.error = \"bad_args\";\n\t\t\treturn true;\n\t\t}\n\n\t\tint mouseButton",
                "\t\tif (!command.args || command.args.path == \"\")\n\t\t{\n\t\t\tresult.ok = false;\n\t\t\tresult.error = \"bad_args\";\n\t\t\treturn true;\n\t\t}\n\t\tBeginUiRequest(command.args, result);\n\n\t\tint mouseButton",
            ),
            "focus_matched_path_before_resolution": _mutate(
                source,
                "\t\tresult.found = true;\n\t\tFillUiMatchedPath(target, result);\n",
                "\t\tresult.found = true;\n",
            ),
        }
        for label, mutant in mutants.items():
            with self.subTest(mutant=label):
                with self.assertRaises(AssertionError):
                    verify_ui_request_echo(mutant)

    def test_click_mode_contract(self) -> None:
        verify_click_mode_contract(self.source)

    def test_click_mode_mutants_are_red(self) -> None:
        source = self.source
        complete_block = (
            '\t\tif (mode == "complete")\n'
            "\t\t{\n"
            "\t\t\tresult.ok = false;\n"
            '\t\t\tresult.error = "mode_not_implemented";\n'
            "\t\t\treturn true;\n"
            "\t\t}\n"
        )
        # (mutant, reason the verifier must give): each mutant is caught for
        # what it actually does, not for an incidental missing literal.
        mutants = {
            "third_mode_accepted": (
                _mutate(
                    source,
                    '\t\tif (mode != "direct")\n\t\t{\n\t\t\tresult.ok = false;\n\t\t\tresult.error = "bad_args";\n\t\t\treturn true;\n\t\t}\n',
                    "",
                ),
                "gates must both exist",
            ),
            # Without its own gate, `complete` is refused by the unknown-mode
            # gate as bad_args: a collapse of the named refusal, not a degrade.
            "complete_collapsed_into_bad_args": (
                _mutate(source, complete_block, ""),
                "gates must both exist",
            ),
            # Rewrites complete into direct and reaches InvokeUiClick: the real
            # degrade the contract forbids.
            "complete_degrades_to_direct": (
                _mutate(
                    source,
                    complete_block,
                    '\t\tif (mode == "complete")\n\t\t{\n\t\t\tmode = "direct";\n\t\t}\n',
                ),
                "complete must return before any handler runs",
            ),
            "complete_dispatches_anyway": (
                _mutate(
                    source,
                    '\t\t\tresult.error = "mode_not_implemented";\n\t\t\treturn true;\n',
                    '\t\t\tresult.error = "mode_not_implemented";\n\t\t\tInvokeUiClick(target, mouseButton, handlerName);\n\t\t\treturn true;\n',
                ),
                "only the direct branch may reach the handler lookup",
            ),
            "direct_reads_bubble": (
                _mutate(
                    source,
                    '\t\tif (mode == "")\n\t\t{\n\t\t\tmode = "direct";\n\t\t}\n',
                    '\t\tif (mode == "")\n\t\t{\n\t\t\tmode = "direct";\n\t\t}\n\t\tif (command.args.bubble)\n\t\t{\n\t\t\tmode = "direct";\n\t\t}\n',
                ),
                "must not read bubble",
            ),
            "mode_checked_before_resolution": (
                _mutate(
                    source,
                    "\t\tstring error = \"\";\n\t\tWidget target = ResolveUiRoot(command.args, error);\n\t\tif (!target)\n\t\t{\n\t\t\tresult.ok = false;\n\t\t\tresult.error = error;\n\t\t\treturn true;\n\t\t}\n\t\tFillUiMatchedPath(target, result);\n\n\t\tstring mode = command.args.mode;\n",
                    "\t\tstring mode = command.args.mode;\n\t\tstring error = \"\";\n\t\tWidget target = ResolveUiRoot(command.args, error);\n\t\tif (!target)\n\t\t{\n\t\t\tresult.ok = false;\n\t\t\tresult.error = error;\n\t\t\treturn true;\n\t\t}\n\t\tFillUiMatchedPath(target, result);\n\n",
                ),
                "missing or out of order",
            ),
        }
        for label, (mutant, reason) in mutants.items():
            with self.subTest(mutant=label):
                with self.assertRaisesRegex(AssertionError, reason):
                    verify_click_mode_contract(mutant)


class CanonicalPathReferenceModelTest(unittest.TestCase):
    """Pins the encoding rule with hand-written expectations (not model output)."""

    def test_percent_encoding_is_bytewise_uppercase_and_encodes_reserved_delimiters(self) -> None:
        self.assertEqual(percent_encode_segment("BtnPreview"), "BtnPreview")
        self.assertEqual(percent_encode_segment("a%b/c@d"), "a%25b%2Fc%40d")
        self.assertEqual(percent_encode_segment("café"), "caf%C3%A9")
        self.assertEqual(percent_encode_segment("-._~"), "-._~")
        self.assertEqual(percent_encode_segment(" "), "%20")
        self.assertEqual(percent_encode_segment(""), "")

    def test_ordinals_are_zero_based_among_byte_identical_siblings(self) -> None:
        chain = [
            ("workspace", ["workspace"]),
            ("SorterRoot", ["Other", "SorterRoot", "SorterRoot"]),
            ("Row", ["Row", "row", "Row"]),
            ("BtnCloseX", ["BtnCloseX"]),
        ]
        self.assertEqual(
            canonical_path(chain),
            "/workspace@0/SorterRoot@1/Row@1/BtnCloseX@0",
        )

    def test_workspace_is_first_segment_at_zero_even_under_a_root_scope(self) -> None:
        chain = [("ws@1", ["ws@1"]), ("Root", ["Root"]), ("Leaf", ["Leaf"])]
        self.assertEqual(canonical_path(chain), "/ws%401@0/Root@0/Leaf@0")
        self.assertTrue(canonical_path(chain).startswith("/ws%401@0"))


if __name__ == "__main__":
    unittest.main()

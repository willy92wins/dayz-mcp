from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote


@dataclass(frozen=True)
class RuntimeHttpFinding:
    relative_path: str
    line: int
    function: str
    kind: str


@dataclass(frozen=True)
class RuntimeHttpAllowance:
    owner: str
    reason: str


@dataclass(frozen=True)
class RuntimeHttpExclusion:
    protocol: str
    owner: str
    reason: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class ProcessCreationFinding:
    relative_path: str
    line: int
    function: str
    kind: str


RUNTIME_HTTP_ALLOWLIST = {
    (
        "dayz_mcp/accredited_daemon_transport.py",
        "verified_daemon_http_request",
        "http_request",
    ): RuntimeHttpAllowance(
        owner="H10 authenticated daemon transport",
        reason="Canonical sink performs socket-owner accreditation before request bytes.",
    ),
    (
        "dayz_mcp/accredited_daemon_transport.py",
        "verified_daemon_http_request",
        "authenticated_query",
    ): RuntimeHttpAllowance(
        owner="H10 authenticated daemon transport",
        reason="Canonical sink forms the authenticated query only after accreditation.",
    ),
    (
        "dayz_mcp/orphan_guard.py",
        "probe_listener_responsive",
        "urlopen",
    ): RuntimeHttpAllowance(
        owner="H10 unauthenticated listener probe",
        reason="Nominal liveness probe sends no API key, identity, lease, or body.",
    ),
}

RUNTIME_HTTP_EXCLUSIONS = {
    "mcp_client.py": RuntimeHttpExclusion(
        protocol="legacy_loopback_harness",
        owner="DayZ_MCP phase-gate harness",
        reason=(
            "Historical phase runner paired only with mcp_server.py's bare loopback; "
            "it is not a normal-daemon discovery, admin, lifecycle, or stdio client."
        ),
        evidence=(
            "run-poc.ps1",
            "run-fase1.ps1",
            "run-fase2.ps1",
            "run-fase3.ps1",
        ),
    )
}

_HTTP_CALLABLES = frozenset(
    {
        "http.client.HTTPConnection",
        "http.client.HTTPSConnection",
        "urllib.parse.urlencode",
        "urllib.request.Request",
        "urllib.request.urlopen",
    }
)
_HTTP_MODULES = frozenset({"http.client", "urllib.parse", "urllib.request"})
_BOUND_HTTP_REQUEST = "http.client.<bound-request>"
_DYNAMIC_HTTP_CALLABLE = "<dynamic-http-callable>"
_POSSIBLE_HTTP_REQUEST = "<possible-http-request>"
_URL_OPENER = "urllib.request.<opener>"
_KEY_MAPPING_TAINT = "\0key-mapping\0"
_AUTHENTICATED_QUERY = re.compile(r"[?&]key(?:=|&|$)", re.IGNORECASE)
_PRODUCTIVE_ROOT_MODULES = frozenset(
    {
        "dayz_mcp.admin_cli",
        "dayz_mcp.doctor",
        "dayz_mcp.lifecycle_cli",
        "dayz_mcp.server",
    }
)


def _attribute_name(node: ast.AST) -> str | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return None


class _HttpVisitor(ast.NodeVisitor):
    def __init__(
        self,
        relative_path: str,
        module: str,
        external_aliases: dict[str, str] | None = None,
    ) -> None:
        self.relative_path = relative_path
        self.module = module
        self.external_aliases = external_aliases or {}
        prefix = f"{module}."
        local_aliases = {
            qualified[len(prefix) :]: value
            for qualified, value in self.external_aliases.items()
            if qualified.startswith(prefix)
            and "." not in qualified[len(prefix) :]
        }
        self.alias_scopes: list[dict[str, str]] = [local_aliases]
        self.text_scopes: list[dict[str, str]] = [{}]
        self.connection_scopes: list[set[str]] = [set()]
        self.class_stack: list[str] = []
        self.function_stack: list[str] = []
        self.findings: list[RuntimeHttpFinding] = []
        self._finding_keys: set[tuple[int, str, str]] = set()
        self._allowance_uses: dict[tuple[str, str, str], int] = {}

    @property
    def function(self) -> str:
        scope = self.class_stack + self.function_stack
        return ".".join(scope) if scope else "<module>"

    @property
    def aliases(self) -> dict[str, str]:
        return self.alias_scopes[-1]

    @property
    def text_bindings(self) -> dict[str, str]:
        return self.text_scopes[-1]

    @property
    def connections(self) -> set[str]:
        return self.connection_scopes[-1]

    def _resolve_name(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.NamedExpr):
            return self._callable_alias_marker(node.value) or self._resolve_name(
                node.value
            )
        name = _attribute_name(node)
        if name is None:
            return None
        head, separator, tail = name.partition(".")
        resolved = self.aliases.get(head, head)
        resolved += separator + tail if separator else ""
        visited: set[str] = set()
        while resolved in self.external_aliases and resolved not in visited:
            visited.add(resolved)
            resolved = self.external_aliases[resolved]
        return resolved

    def _allowed(self, kind: str) -> bool:
        key = (
            self.relative_path,
            self.function,
            kind,
        )
        if key not in RUNTIME_HTTP_ALLOWLIST:
            return False
        uses = self._allowance_uses.get(key, 0)
        self._allowance_uses[key] = uses + 1
        return uses == 0

    def _record(self, node: ast.AST, kind: str, *, force: bool = False) -> None:
        if self._allowed(kind) and not force:
            return
        line = int(getattr(node, "lineno", 0))
        key = (line, self.function, kind)
        if key in self._finding_keys:
            return
        self._finding_keys.add(key)
        self.findings.append(
            RuntimeHttpFinding(self.relative_path, line, self.function, kind)
        )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.asname:
                self.aliases[alias.asname] = alias.name
            else:
                head = alias.name.split(".", 1)[0]
                self.aliases.setdefault(head, head)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        imported = _import_from_module(
            self.module,
            node,
            is_package=self.relative_path.endswith("/__init__.py"),
        )
        for alias in node.names:
            if alias.name == "*":
                if imported in _HTTP_MODULES or imported.startswith("urllib"):
                    self._record(node, "dynamic_http", force=True)
                continue
            self.aliases[alias.asname or alias.name] = f"{imported}.{alias.name}"

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_stack.append(node.name)
        self.alias_scopes.append(dict(self.aliases))
        self.text_scopes.append(dict(self.text_bindings))
        self.generic_visit(node)
        self.text_scopes.pop()
        self.alias_scopes.pop()
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.function_stack.append(node.name)
        self.alias_scopes.append(dict(self.aliases))
        self.text_scopes.append(dict(self.text_bindings))
        self.connection_scopes.append(set())
        self._drop_function_local_aliases(node)
        if self._probe_violates_nominal_grammar(node):
            self._record(node, "sensitive_probe", force=True)
        self.generic_visit(node)
        self.connection_scopes.pop()
        self.text_scopes.pop()
        self.alias_scopes.pop()
        self.function_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.function_stack.append(node.name)
        self.alias_scopes.append(dict(self.aliases))
        self.text_scopes.append(dict(self.text_bindings))
        self.connection_scopes.append(set())
        self._drop_function_local_aliases(node)
        if self._probe_violates_nominal_grammar(node):
            self._record(node, "sensitive_probe", force=True)
        self.generic_visit(node)
        self.connection_scopes.pop()
        self.text_scopes.pop()
        self.alias_scopes.pop()
        self.function_stack.pop()

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.function_stack.append("<lambda>")
        self.alias_scopes.append(dict(self.aliases))
        self.text_scopes.append(dict(self.text_bindings))
        self.connection_scopes.append(set())
        self._drop_binding_names(self._argument_names(node.args))
        self.generic_visit(node)
        self.connection_scopes.pop()
        self.text_scopes.pop()
        self.alias_scopes.pop()
        self.function_stack.pop()

    @staticmethod
    def _argument_names(arguments: ast.arguments) -> set[str]:
        names = {
            argument.arg
            for argument in (
                list(arguments.posonlyargs)
                + list(arguments.args)
                + list(arguments.kwonlyargs)
            )
        }
        if arguments.vararg is not None:
            names.add(arguments.vararg.arg)
        if arguments.kwarg is not None:
            names.add(arguments.kwarg.arg)
        return names

    def _drop_binding_names(self, names: set[str]) -> None:
        for name in names:
            self.aliases.pop(name, None)
            self.text_bindings.pop(name, None)

    def _drop_function_local_aliases(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> None:
        local_names = self._argument_names(node.args)

        class LocalStoreCollector(ast.NodeVisitor):
            def __init__(self) -> None:
                self.names: set[str] = set()

            def visit_Name(self, child: ast.Name) -> None:
                if isinstance(child.ctx, ast.Store):
                    self.names.add(child.id)

            def visit_FunctionDef(self, child: ast.FunctionDef) -> None:
                return

            def visit_AsyncFunctionDef(self, child: ast.AsyncFunctionDef) -> None:
                return

            def visit_ClassDef(self, child: ast.ClassDef) -> None:
                return

            def visit_Lambda(self, child: ast.Lambda) -> None:
                return

            def _visit_comprehension(self, child: ast.AST) -> None:
                generators = getattr(child, "generators", ())
                if generators:
                    self.visit(generators[0].iter)

            visit_ListComp = _visit_comprehension
            visit_SetComp = _visit_comprehension
            visit_DictComp = _visit_comprehension
            visit_GeneratorExp = _visit_comprehension

        collector = LocalStoreCollector()
        for statement in node.body:
            collector.visit(statement)
        local_names.update(collector.names)
        self._drop_binding_names(local_names)

    @staticmethod
    def _targets(node: ast.Assign | ast.AnnAssign) -> list[ast.expr]:
        if isinstance(node, ast.Assign):
            return list(node.targets)
        return [node.target]

    def _callable_alias_marker(self, value: ast.AST | None) -> str | None:
        if value is None:
            return None
        if isinstance(value, (ast.Starred, ast.NamedExpr)):
            return self._callable_alias_marker(value.value)
        if (
            isinstance(value, ast.Call)
            and self._resolve_name(value.func) == "urllib.request.build_opener"
        ):
            return _URL_OPENER
        if isinstance(value, ast.Call) and self._resolve_name(value.func) == "getattr":
            return _DYNAMIC_HTTP_CALLABLE
        request_attributes = [
            child
            for child in ast.walk(value)
            if isinstance(child, ast.Attribute) and child.attr == "request"
        ]
        if request_attributes:
            definite = any(
                (
                    (owner := _attribute_name(attribute.value)) is not None
                    and owner in self.connections
                )
                or (
                    isinstance(attribute.value, ast.Call)
                    and self._resolve_name(attribute.value.func)
                    in {
                        "http.client.HTTPConnection",
                        "http.client.HTTPSConnection",
                    }
                )
                for attribute in request_attributes
            )
            return _BOUND_HTTP_REQUEST if definite else _POSSIBLE_HTTP_REQUEST
        if not isinstance(value, (ast.Name, ast.Attribute)):
            return None
        resolved = self._resolve_name(value)
        if resolved in _HTTP_CALLABLES | _HTTP_MODULES | {
            _BOUND_HTTP_REQUEST,
            _DYNAMIC_HTTP_CALLABLE,
            _POSSIBLE_HTTP_REQUEST,
            _URL_OPENER,
        }:
            return resolved
        if (
            isinstance(resolved, str)
            and resolved not in _HTTP_MODULES
            and resolved.endswith(".request")
        ):
            return _POSSIBLE_HTTP_REQUEST
        return None

    def _track_callable_alias_assignment(
        self, node: ast.Assign | ast.AnnAssign
    ) -> None:
        marker = self._callable_alias_marker(node.value)
        for target in self._targets(node):
            if isinstance(target, ast.Name):
                if marker is None:
                    self.aliases.pop(target.id, None)
                else:
                    self.aliases[target.id] = marker

    def _track_connection_assignment(
        self, node: ast.Assign | ast.AnnAssign
    ) -> None:
        value = node.value
        if value is None:
            return
        is_connection = (
            isinstance(value, ast.Call)
            and self._resolve_name(value.func)
            in {
                "http.client.HTTPConnection",
                "http.client.HTTPSConnection",
            }
        ) or ((_attribute_name(value) or "") in self.connections)
        if not is_connection:
            return
        for target in self._targets(node):
            target_name = _attribute_name(target)
            if target_name is not None:
                self.connections.add(target_name)

    def _assigned_text(self, value: ast.AST | None) -> str:
        if value is None:
            return ""
        text = self._constant_text_with_bindings(value, self.text_bindings)
        if self._contains_key_mapping(value):
            return _KEY_MAPPING_TAINT
        elif (
            isinstance(value, ast.Call)
            and self._resolve_name(value.func) == "urllib.parse.urlencode"
        ):
            encoded_input = "".join(
                self._constant_text_with_bindings(item, self.text_bindings)
                for item in (
                    list(value.args) + [keyword.value for keyword in value.keywords]
                )
            ).casefold()
            if (
                self._contains_key_mapping(value)
                or _KEY_MAPPING_TAINT in encoded_input
            ):
                return "key="
        return text

    def _bind_text_targets(self, targets: list[ast.expr], text: str) -> None:
        for target in targets:
            if isinstance(target, ast.Name):
                self.text_bindings[target.id] = text
            elif (
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and isinstance(target.slice, ast.Constant)
                and target.slice.value == "key"
            ):
                self.text_bindings[target.value.id] = _KEY_MAPPING_TAINT

    def _track_text_assignment(
        self, node: ast.Assign | ast.AnnAssign
    ) -> None:
        self._bind_text_targets(self._targets(node), self._assigned_text(node.value))

    def visit_Assign(self, node: ast.Assign) -> None:
        self._track_connection_assignment(node)
        self._track_callable_alias_assignment(node)
        self._track_text_assignment(node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._track_connection_assignment(node)
        self._track_callable_alias_assignment(node)
        self._track_text_assignment(node)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        if isinstance(node.target, ast.Name):
            marker = self._callable_alias_marker(node.value)
            if marker is None:
                self.aliases.pop(node.target.id, None)
            else:
                self.aliases[node.target.id] = marker
            self.text_bindings[node.target.id] = self._assigned_text(node.value)
        self.visit(node.value)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if isinstance(node.target, ast.Name):
            self.aliases.pop(node.target.id, None)
            previous = self.text_bindings.get(node.target.id, "")
            self.text_bindings[node.target.id] = previous + self._assigned_text(
                node.value
            )
        else:
            self._bind_text_targets([node.target], self._assigned_text(node.value))
        self.generic_visit(node)

    @staticmethod
    def _target_names(target: ast.AST) -> set[str]:
        return {
            child.id
            for child in ast.walk(target)
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
        }

    def _bind_comprehension_target(
        self,
        target: ast.AST,
        marker: str | None,
        text: str,
    ) -> None:
        names = self._target_names(target)
        self._drop_binding_names(names)
        self.connections.difference_update(names)
        for name in names:
            if marker is not None:
                self.aliases[name] = marker
            if text:
                self.text_bindings[name] = text

    @staticmethod
    def _comprehension_namedexpr_names(node: ast.AST) -> set[str]:
        class NamedExprCollector(ast.NodeVisitor):
            def __init__(self) -> None:
                self.names: set[str] = set()

            def visit_NamedExpr(self, expression: ast.NamedExpr) -> None:
                if isinstance(expression.target, ast.Name):
                    self.names.add(expression.target.id)
                self.visit(expression.value)

            def visit_Lambda(self, expression: ast.Lambda) -> None:
                return

            def visit_FunctionDef(self, statement: ast.FunctionDef) -> None:
                return

            def visit_AsyncFunctionDef(self, statement: ast.AsyncFunctionDef) -> None:
                return

            def visit_ClassDef(self, statement: ast.ClassDef) -> None:
                return

        collector = NamedExprCollector()
        collector.visit(node)
        return collector.names

    def _visit_comprehension(
        self,
        node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
        results: tuple[ast.AST, ...],
    ) -> None:
        first = node.generators[0]
        self.visit(first.iter)
        first_marker = self._callable_alias_marker(first.iter)
        first_text = self._constant_text_with_bindings(
            first.iter, self.text_bindings
        )
        self.alias_scopes.append(dict(self.aliases))
        self.text_scopes.append(dict(self.text_bindings))
        self.connection_scopes.append(set(self.connections))
        self._bind_comprehension_target(first.target, first_marker, first_text)
        self.visit(first.target)
        for condition in first.ifs:
            self.visit(condition)
        for generator in node.generators[1:]:
            self.visit(generator.iter)
            marker = self._callable_alias_marker(generator.iter)
            text = self._constant_text_with_bindings(
                generator.iter, self.text_bindings
            )
            self._bind_comprehension_target(generator.target, marker, text)
            self.visit(generator.target)
            for condition in generator.ifs:
                self.visit(condition)
        for result in results:
            self.visit(result)
        export_names = self._comprehension_namedexpr_names(node)
        exported_aliases = {
            name: self.aliases[name] for name in export_names if name in self.aliases
        }
        exported_text = {
            name: self.text_bindings.get(name, "") for name in export_names
        }
        self.connection_scopes.pop()
        self.text_scopes.pop()
        self.alias_scopes.pop()
        for name in export_names:
            if name in exported_aliases:
                self.aliases[name] = exported_aliases[name]
            else:
                self.aliases.pop(name, None)
            self.text_bindings[name] = exported_text[name]

    @staticmethod
    def _merge_alias_states(*states: dict[str, str]) -> dict[str, str]:
        merged: dict[str, str] = {}
        priority = {
            "urllib.request.urlopen": 0,
            "urllib.request.Request": 1,
            _BOUND_HTTP_REQUEST: 2,
            _DYNAMIC_HTTP_CALLABLE: 3,
            _POSSIBLE_HTTP_REQUEST: 4,
            _URL_OPENER: 5,
        }
        for name in set().union(*(state.keys() for state in states)):
            values = [state[name] for state in states if name in state]
            merged[name] = min(values, key=lambda value: (priority.get(value, 6), value))
        return merged

    @staticmethod
    def _merge_text_states(*states: dict[str, str]) -> dict[str, str]:
        merged: dict[str, str] = {}
        for name in set().union(*(state.keys() for state in states)):
            values = list(dict.fromkeys(state[name] for state in states if name in state))
            merged[name] = "\0".join(value for value in values if value)
        return merged

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        base_aliases = dict(self.alias_scopes[-1])
        base_text = dict(self.text_scopes[-1])
        base_connections = set(self.connection_scopes[-1])

        def visit_branch(statements: list[ast.stmt]) -> tuple[dict[str, str], dict[str, str], set[str]]:
            self.alias_scopes[-1] = dict(base_aliases)
            self.text_scopes[-1] = dict(base_text)
            self.connection_scopes[-1] = set(base_connections)
            for statement in statements:
                self.visit(statement)
            return (
                dict(self.alias_scopes[-1]),
                dict(self.text_scopes[-1]),
                set(self.connection_scopes[-1]),
            )

        body_aliases, body_text, body_connections = visit_branch(node.body)
        if node.orelse:
            else_aliases, else_text, else_connections = visit_branch(node.orelse)
        else:
            else_aliases = base_aliases
            else_text = base_text
            else_connections = base_connections

        self.alias_scopes[-1] = self._merge_alias_states(body_aliases, else_aliases)
        self.text_scopes[-1] = self._merge_text_states(body_text, else_text)
        self.connection_scopes[-1] = body_connections | else_connections

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node, (node.elt,))

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node, (node.elt,))

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node, (node.key, node.value))

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node, (node.elt,))

    def _contains_key_mapping(self, node: ast.AST) -> bool:
        for child in ast.walk(node):
            if isinstance(child, ast.Dict) and any(
                isinstance(key, ast.Constant) and key.value == "key"
                for key in child.keys
            ):
                return True
            if (
                isinstance(child, ast.Call)
                and self._resolve_name(child.func) == "dict"
                and any(keyword.arg == "key" for keyword in child.keywords)
            ):
                return True
        return False

    @classmethod
    def _constant_text(cls, node: ast.AST) -> str:
        if isinstance(node, (ast.Starred, ast.NamedExpr)):
            return cls._constant_text(node.value)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.FormattedValue):
            return ""
        if isinstance(node, ast.JoinedStr):
            return "".join(cls._constant_text(value) for value in node.values)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return cls._constant_text(node.left) + cls._constant_text(node.right)
        if isinstance(node, ast.IfExp):
            return cls._constant_text(node.body) + cls._constant_text(node.orelse)
        if isinstance(node, ast.BoolOp):
            return "".join(cls._constant_text(value) for value in node.values)
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return "".join(cls._constant_text(value) for value in node.elts)
        return ""

    @classmethod
    def _constant_text_with_bindings(
        cls, node: ast.AST, bindings: dict[str, str]
    ) -> str:
        if isinstance(node, (ast.Starred, ast.NamedExpr)):
            return cls._constant_text_with_bindings(node.value, bindings)
        if isinstance(node, ast.Name):
            return bindings.get(node.id, "")
        if isinstance(node, ast.JoinedStr):
            return "".join(
                cls._constant_text_with_bindings(value, bindings)
                for value in node.values
            )
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return cls._constant_text_with_bindings(
                node.left, bindings
            ) + cls._constant_text_with_bindings(node.right, bindings)
        if isinstance(node, ast.IfExp):
            return cls._constant_text_with_bindings(
                node.body, bindings
            ) + cls._constant_text_with_bindings(node.orelse, bindings)
        if isinstance(node, ast.BoolOp):
            return "".join(
                cls._constant_text_with_bindings(value, bindings)
                for value in node.values
            )
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return "".join(
                cls._constant_text_with_bindings(value, bindings)
                for value in node.elts
            )
        return cls._constant_text(node)

    def _contains_authenticated_query(self, node: ast.Call) -> bool:
        values = list(node.args) + [keyword.value for keyword in node.keywords]
        static = "".join(
            self._constant_text_with_bindings(value, self.text_bindings)
            for value in values
        )
        return _AUTHENTICATED_QUERY.search(unquote(static)) is not None

    @staticmethod
    def _has_http_call_shape(node: ast.Call) -> bool:
        return (
            len(node.args) >= 2
            or any(isinstance(argument, ast.Starred) for argument in node.args)
            or any(
                keyword.arg is None or keyword.arg in {"method", "url"}
                for keyword in node.keywords
            )
        )

    @staticmethod
    def _has_sensitive_body(node: ast.Call) -> bool:
        if len(node.args) >= 2 and not (
            isinstance(node.args[1], ast.Constant) and node.args[1].value is None
        ):
            return True
        return any(
            keyword.arg in {"body", "data"}
            and not (
                isinstance(keyword.value, ast.Constant)
                and keyword.value.value is None
            )
            for keyword in node.keywords
        )

    @staticmethod
    def _same_scope_nodes(
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> tuple[ast.AST, ...]:
        nested_scopes = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)

        def descend(current: ast.AST):
            if isinstance(current, nested_scopes):
                return
            yield current
            for child in ast.iter_child_nodes(current):
                yield from descend(child)

        statements = (
            node.body[1:]
            if ast.get_docstring(node, clean=False) is not None
            else node.body
        )
        return tuple(
            child
            for statement in statements
            for child in descend(statement)
        )

    @staticmethod
    def _is_nominal_probe_url(node: ast.AST) -> bool:
        if not isinstance(node, ast.JoinedStr) or len(node.values) != 5:
            return False
        prefix, host, separator, port, suffix = node.values
        if not (
            isinstance(prefix, ast.Constant)
            and prefix.value == "http://"
            and isinstance(separator, ast.Constant)
            and separator.value == ":"
            and isinstance(suffix, ast.Constant)
            and suffix.value == "/status"
        ):
            return False
        if not (
            isinstance(host, ast.FormattedValue)
            and host.conversion == -1
            and host.format_spec is None
            and isinstance(host.value, ast.Name)
            and host.value.id == "host"
        ):
            return False
        return (
            isinstance(port, ast.FormattedValue)
            and port.conversion == -1
            and port.format_spec is None
            and isinstance(port.value, ast.Call)
            and isinstance(port.value.func, ast.Name)
            and port.value.func.id == "int"
            and len(port.value.args) == 1
            and isinstance(port.value.args[0], ast.Name)
            and port.value.args[0].id == "port"
            and not port.value.keywords
        )

    @staticmethod
    def _is_nominal_probe_urlopen(node: ast.Call) -> bool:
        return (
            len(node.args) == 1
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "url"
            and len(node.keywords) == 1
            and node.keywords[0].arg == "timeout"
            and isinstance(node.keywords[0].value, ast.Name)
            and node.keywords[0].value.id == "timeout"
        )

    def _probe_violates_nominal_grammar(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> bool:
        if (
            self.relative_path != "dayz_mcp/orphan_guard.py"
            or node.name != "probe_listener_responsive"
            or self.class_stack
            or self.function_stack != ["probe_listener_responsive"]
        ):
            return False
        if not isinstance(node, ast.FunctionDef):
            return True
        if (
            node.args.posonlyargs
            or [argument.arg for argument in node.args.args] != ["port"]
            or [argument.arg for argument in node.args.kwonlyargs]
            != ["timeout", "host"]
            or node.args.vararg is not None
            or node.args.kwarg is not None
        ):
            return True
        timeout_default, host_default = node.args.kw_defaults
        if (
            node.args.defaults
            or not isinstance(timeout_default, ast.Constant)
            or type(timeout_default.value) is not float
            or timeout_default.value != 1.0
            or not isinstance(host_default, ast.Constant)
            or type(host_default.value) is not str
            or host_default.value != "127.0.0.1"
        ):
            return True
        nodes = self._same_scope_nodes(node)
        assignments = [
            child
            for child in nodes
            if isinstance(child, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr))
        ]
        if len(assignments) != 1 or assignments[0] not in node.body:
            return True
        assignment = assignments[0]
        if not isinstance(assignment, ast.Assign):
            return True
        targets = self._targets(assignment)
        if not (
            len(targets) == 1
            and isinstance(targets[0], ast.Name)
            and targets[0].id == "url"
            and self._is_nominal_probe_url(assignment.value)
        ):
            return True
        if any(
            isinstance(child, (ast.Dict, ast.DictComp, ast.IfExp, ast.BoolOp, ast.Subscript))
            for child in nodes
        ):
            return True
        for child in nodes:
            if not isinstance(child, ast.Constant) or not isinstance(child.value, str):
                continue
            text = child.value.casefold()
            if any(
                marker in text
                for marker in ("?", "&", "#", "key", "token", "identity", "lease")
            ):
                return True
        calls = [child for child in nodes if isinstance(child, ast.Call)]
        urlopens = [
            call
            for call in calls
            if self._resolve_name(call.func) == "urllib.request.urlopen"
        ]
        if len(urlopens) != 1 or not self._is_nominal_probe_urlopen(urlopens[0]):
            return True
        for call in calls:
            name = self._resolve_name(call.func)
            if name in {"dict", "urllib.parse.urlencode", "urllib.request.Request"}:
                return True
            if (
                name in {_BOUND_HTTP_REQUEST, _DYNAMIC_HTTP_CALLABLE}
                and self._has_http_call_shape(call)
            ):
                return True
            if isinstance(call.func, ast.Attribute) and call.func.attr == "request":
                return True
            if (
                isinstance(call.func, ast.Call)
                and self._resolve_name(call.func.func) == "getattr"
                and self._has_http_call_shape(call)
            ):
                return True
            if name == "urllib.request.urlopen" and call in urlopens:
                continue
            if (
                name == "int"
                and len(call.args) == 1
                and not call.keywords
                and (
                    isinstance(call.args[0], ast.Name)
                    and call.args[0].id == "port"
                    or isinstance(call.args[0], ast.Attribute)
                    and isinstance(call.args[0].value, ast.Name)
                    and call.args[0].value.id == "response"
                    and call.args[0].attr == "status"
                )
            ):
                continue
            if (
                isinstance(call.func, ast.Attribute)
                and call.func.attr == "close"
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "exc"
                and not call.args
                and not call.keywords
            ):
                continue
            return True
        return False

    def visit_Call(self, node: ast.Call) -> None:
        name = self._resolve_name(node.func)
        direct_request = (
            isinstance(node.func, ast.Attribute) and node.func.attr == "request"
        )
        direct_owner = (
            _attribute_name(node.func.value)
            if isinstance(node.func, ast.Attribute)
            else None
        )
        tracked_direct_request = direct_request and direct_owner in self.connections
        resolved_request_alias = (
            not direct_request
            and isinstance(name, str)
            and name.endswith(".request")
        )
        http_shape = self._has_http_call_shape(node)
        dynamic_getattr = (
            isinstance(node.func, ast.Call)
            and self._resolve_name(node.func.func) == "getattr"
            and http_shape
        )
        low_level_http = (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in {"putrequest", "putheader", "endheaders", "send"}
            and direct_owner in self.connections
        )
        embedded_bound_request = any(
            isinstance(child, ast.Attribute)
            and child.attr == "request"
            and (_attribute_name(child.value) or "") in self.connections
            for child in ast.walk(node.func)
        )
        opener_call = name == f"{_URL_OPENER}.open" or (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "open"
            and isinstance(node.func.value, ast.Call)
            and self._resolve_name(node.func.value.func)
            == "urllib.request.build_opener"
        )
        dynamic_import = (
            name == "__import__"
            and bool(node.args)
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value
            in {"http.client", "urllib", "urllib.parse", "urllib.request"}
        )
        if name == "urllib.request.urlopen":
            self._record(node, "urlopen")
        if name in {"urllib.request.Request", "urllib.request.urlopen"}:
            if self._has_sensitive_body(node):
                self._record(node, "sensitive_body")
        query_sink = (
            name in _HTTP_CALLABLES
            or tracked_direct_request
            or (direct_request and http_shape)
            or (resolved_request_alias and http_shape)
            or dynamic_getattr
            or (
                name
                in {
                    _BOUND_HTTP_REQUEST,
                    _DYNAMIC_HTTP_CALLABLE,
                    _POSSIBLE_HTTP_REQUEST,
                }
                and (name == _BOUND_HTTP_REQUEST or http_shape)
            )
            or low_level_http
            or opener_call
        )
        if query_sink and self._contains_authenticated_query(node):
            self._record(node, "authenticated_query")
        if name == _BOUND_HTTP_REQUEST:
            self._record(node, "http_request")
        elif name == _DYNAMIC_HTTP_CALLABLE and http_shape:
            self._record(node, "dynamic_http")
        elif name == _POSSIBLE_HTTP_REQUEST and http_shape:
            self._record(node, "http_request")
        elif resolved_request_alias and http_shape:
            self._record(node, "http_request")
        elif name == "urllib.parse.urlencode" and self._contains_key_mapping(node):
            self._record(node, "authenticated_query")
        elif tracked_direct_request or (direct_request and http_shape):
            self._record(node, "http_request")
        elif low_level_http or embedded_bound_request:
            self._record(node, "http_request")
        if dynamic_getattr or opener_call or dynamic_import:
            self._record(node, "dynamic_http")
        self.generic_visit(node)


def _module_name(tools_dir: Path, path: Path) -> str:
    relative = path.relative_to(tools_dir).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _import_from_module(
    module: str,
    node: ast.ImportFrom,
    *,
    is_package: bool = False,
) -> str:
    if not node.level:
        return node.module or ""
    package = module if is_package else module.rpartition(".")[0]
    base_parts = package.split(".") if package else []
    trim = max(0, node.level - 1)
    if trim:
        base_parts = base_parts[:-trim]
    return ".".join(
        part for part in (".".join(base_parts), node.module or "") if part
    )


def _imported_modules(
    tree: ast.Module, module: str, available: frozenset[str]
) -> set[str]:
    dependencies: set[str] = set()
    is_package = any(name.startswith(f"{module}.") for name in available)
    type_checking_names = {
        alias.asname or alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "typing"
        for alias in node.names
        if alias.name == "TYPE_CHECKING"
    }
    typing_module_names = {
        alias.asname or alias.name
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "typing"
    }
    rebound_names = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }
    rebound_names.update(
        node.arg for node in ast.walk(tree) if isinstance(node, ast.arg)
    )
    rebound_names.update(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    )
    trusted_type_checking_names = type_checking_names - rebound_names
    trusted_typing_module_names = typing_module_names - rebound_names

    def is_type_checking_only(node: ast.AST) -> bool:
        return bool(
            isinstance(node, ast.Name)
            and node.id in trusted_type_checking_names
            or isinstance(node, ast.Attribute)
            and node.attr == "TYPE_CHECKING"
            and isinstance(node.value, ast.Name)
            and node.value.id in trusted_typing_module_names
        )

    class RuntimeImportCollector(ast.NodeVisitor):
        def visit_If(self, node: ast.If) -> None:
            if is_type_checking_only(node.test):
                for statement in node.orelse:
                    self.visit(statement)
                return
            self.generic_visit(node)

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                if alias.name in available:
                    dependencies.add(alias.name)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            imported = _import_from_module(module, node, is_package=is_package)
            candidates = [imported]
            candidates.extend(
                f"{imported}.{alias.name}" for alias in node.names if imported
            )
            dependencies.update(
                candidate for candidate in candidates if candidate in available
            )

    RuntimeImportCollector().visit(tree)
    return dependencies


def _runtime_sources(tools_dir: Path) -> tuple[Path, ...]:
    package = tools_dir / "dayz_mcp"
    sources = list(package.rglob("*.py")) if package.is_dir() else []
    legacy_candidate = tools_dir / "mcp_client.py"
    if legacy_candidate.is_file():
        sources.append(legacy_candidate)
    modules = {_module_name(tools_dir, path): path for path in sources}
    available = frozenset(modules)
    roots = {name for name in _PRODUCTIVE_ROOT_MODULES if name in modules}
    if not roots:
        selected = set(modules)
    else:
        selected: set[str] = set()
        pending = list(roots)
        while pending:
            name = pending.pop()
            if name in selected:
                continue
            selected.add(name)
            try:
                tree = ast.parse(
                    modules[name].read_text(encoding="utf-8"),
                    filename=str(modules[name]),
                )
            except (OSError, UnicodeError, SyntaxError):
                continue
            pending.extend(_imported_modules(tree, name, available) - selected)
    if "mcp_client" in modules:
        selected.add("mcp_client")
    return tuple(
        sorted(
            (modules[name] for name in selected),
            key=lambda path: path.as_posix().casefold(),
        )
    )


def _module_aliases(
    module: str,
    tree: ast.Module,
    *,
    is_package: bool = False,
) -> dict[str, str]:
    local: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                local[alias.asname or alias.name.split(".", 1)[0]] = (
                    alias.name if alias.asname else alias.name.split(".", 1)[0]
                )
        elif isinstance(node, ast.ImportFrom):
            imported = _import_from_module(
                module, node, is_package=is_package
            )
            for alias in node.names:
                if alias.name != "*":
                    local[alias.asname or alias.name] = f"{imported}.{alias.name}"
    changed = True
    while changed:
        changed = False
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if not isinstance(value, (ast.Name, ast.Attribute)):
                continue
            raw = _attribute_name(value)
            if raw is None:
                continue
            head, separator, tail = raw.partition(".")
            resolved = local.get(head, head) + (separator + tail if separator else "")
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and local.get(target.id) != resolved:
                    local[target.id] = resolved
                    changed = True
    return {f"{module}.{name}": value for name, value in local.items()}


def audit_runtime_http(tools_dir: Path) -> tuple[RuntimeHttpFinding, ...]:
    tools_dir = Path(tools_dir)
    findings: list[RuntimeHttpFinding] = []
    parsed: list[tuple[Path, str, ast.Module]] = []
    for path in _runtime_sources(tools_dir):
        relative = path.relative_to(tools_dir).as_posix()
        if relative in RUNTIME_HTTP_EXCLUSIONS:
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, UnicodeError, SyntaxError):
            findings.append(
                RuntimeHttpFinding(relative, 0, "<module>", "parse_error")
            )
            continue
        parsed.append((path, relative, tree))
    external_aliases: dict[str, str] = {}
    for path, _relative, tree in parsed:
        external_aliases.update(
            _module_aliases(
                _module_name(tools_dir, path),
                tree,
                is_package=path.name == "__init__.py",
            )
        )
    for path, relative, tree in parsed:
        visitor = _HttpVisitor(
            relative,
            _module_name(tools_dir, path),
            external_aliases,
        )
        visitor.visit(tree)
        findings.extend(visitor.findings)
    return tuple(
        sorted(
            findings,
            key=lambda item: (
                item.relative_path.casefold(),
                item.line,
                item.function,
                item.kind,
            ),
        )
    )


_PROCESS_AUDIT_PATHS = frozenset(
    {
        "dayz_mcp/native_launcher_backend.py",
        "dayz_mcp/secure_launcher.py",
    }
)
_PROCESS_CALLABLES = frozenset(
    {
        "asyncio.create_subprocess_exec",
        "asyncio.create_subprocess_shell",
        "os.popen",
        "os.startfile",
        "os.system",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "subprocess.getoutput",
        "subprocess.getstatusoutput",
        "subprocess.Popen",
        "subprocess.run",
    }
)
_PROCESS_MODULES = frozenset({"asyncio", "os", "subprocess"})
_DYNAMIC_PROCESS_CALLABLES = frozenset(
    {
        "__import__",
        "builtins.__import__",
        "builtins.compile",
        "builtins.eval",
        "builtins.exec",
        "builtins.getattr",
        "compile",
        "eval",
        "exec",
        "getattr",
        "importlib.import_module",
    }
)
_SHADOWED_PROCESS_NAME = "\0shadowed-process-name\0"
_PROCESS_TERMINALS = frozenset(
    {
        "createprocess",
        "createprocessa",
        "createprocessw",
        "shellexecute",
        "shellexecutea",
        "shellexecutew",
        "winexec",
    }
)


class _ProcessCreationVisitor(ast.NodeVisitor):
    def __init__(self, relative_path: str) -> None:
        self.relative_path = relative_path
        self.alias_scopes: list[dict[str, str]] = [{}]
        self.class_stack: list[str] = []
        self.function_stack: list[str] = []
        self.findings: list[ProcessCreationFinding] = []
        self.direct_create_process_calls = 0
        self.canonical_create_flags_definitions = 0

    @property
    def aliases(self) -> dict[str, str]:
        return self.alias_scopes[-1]

    @property
    def function(self) -> str:
        scope = self.class_stack + self.function_stack
        return ".".join(scope) if scope else "<module>"

    def _record(self, node: ast.AST, kind: str) -> None:
        self.findings.append(
            ProcessCreationFinding(
                self.relative_path,
                int(getattr(node, "lineno", 0)),
                self.function,
                kind,
            )
        )

    @staticmethod
    def _is_name(node: ast.AST, expected: str) -> bool:
        return isinstance(node, ast.Name) and node.id == expected

    @staticmethod
    def _is_none(node: ast.AST) -> bool:
        return isinstance(node, ast.Constant) and node.value is None

    @staticmethod
    def _is_true(node: ast.AST) -> bool:
        return isinstance(node, ast.Constant) and node.value is True

    @classmethod
    def _creation_flag_names(cls, node: ast.AST) -> tuple[str, ...] | None:
        if isinstance(node, ast.Name):
            return (node.id,)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            left = cls._creation_flag_names(node.left)
            right = cls._creation_flag_names(node.right)
            if left is not None and right is not None:
                return left + right
        return None

    @staticmethod
    def _is_byref(node: ast.AST, expected: str) -> bool:
        return (
            isinstance(node, ast.Call)
            and not node.keywords
            and len(node.args) == 1
            and _attribute_name(node.func) == "ctypes.byref"
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == expected
        )

    @classmethod
    def _is_canonical_create_process_call(cls, node: ast.Call) -> bool:
        return bool(
            _attribute_name(node.func) == "_kernel32.CreateProcessW"
            and not node.keywords
            and len(node.args) == 10
            and cls._is_name(node.args[0], "application_name")
            and cls._is_name(node.args[1], "command_line")
            and cls._is_none(node.args[2])
            and cls._is_none(node.args[3])
            and cls._is_true(node.args[4])
            and cls._is_name(node.args[5], "CREATE_FLAGS")
            and cls._is_name(node.args[6], "environment_block")
            and cls._is_name(node.args[7], "current_directory")
            and cls._is_byref(node.args[8], "startup_info")
            and cls._is_byref(node.args[9], "process_info")
        )

    @staticmethod
    def _contains_windows_process_loader(node: ast.AST) -> bool:
        for child in ast.walk(node):
            name = _attribute_name(child)
            if name is not None and (
                name.startswith("ctypes.WinDLL")
                or name.startswith("ctypes.CDLL")
                or name.startswith("ctypes.windll")
                or name.startswith("ctypes.cdll")
                or name.startswith("_winapi")
            ):
                return True
        return False

    def _is_dynamic_process_lookup(self, node: ast.Call) -> bool:
        if len(node.args) < 2:
            return False
        owner = self._resolve(node.args[0]) or _attribute_name(node.args[0])
        name_node = node.args[1]
        member = name_node.value if isinstance(name_node, ast.Constant) else None
        if isinstance(member, str):
            if member.casefold() in _PROCESS_TERMINALS:
                return True
            resolved_member = f"{owner}.{member}" if owner else member
            return bool(
                resolved_member in _PROCESS_CALLABLES
                or resolved_member.startswith("os.spawn")
            )
        return bool(
            owner is not None
            and (
                owner in _PROCESS_MODULES
                or owner.startswith(tuple(module + "." for module in _PROCESS_MODULES))
                or owner.startswith("ctypes")
                or owner.startswith("_winapi")
            )
            or self._contains_windows_process_loader(node.args[0])
        )

    def _resolve(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.NamedExpr):
            return self._resolve(node.value)
        raw = _attribute_name(node)
        if raw is None:
            return None
        head, separator, tail = raw.partition(".")
        return self.aliases.get(head, head) + (separator + tail if separator else "")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.aliases[alias.asname or alias.name.split(".", 1)[0]] = (
                alias.name if alias.asname else alias.name.split(".", 1)[0]
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            if alias.name == "*":
                if module in _PROCESS_MODULES:
                    self._record(node, "process_star_import")
                continue
            self.aliases[alias.asname or alias.name] = f"{module}.{alias.name}"

    @staticmethod
    def _argument_names(arguments: ast.arguments) -> set[str]:
        names = {
            argument.arg
            for argument in (
                list(arguments.posonlyargs)
                + list(arguments.args)
                + list(arguments.kwonlyargs)
            )
        }
        if arguments.vararg is not None:
            names.add(arguments.vararg.arg)
        if arguments.kwarg is not None:
            names.add(arguments.kwarg.arg)
        return names

    @staticmethod
    def _function_local_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
        class LocalStoreCollector(ast.NodeVisitor):
            def __init__(self) -> None:
                self.names: set[str] = set()

            def visit_Name(self, child: ast.Name) -> None:
                if isinstance(child.ctx, ast.Store):
                    self.names.add(child.id)

            def visit_FunctionDef(self, child: ast.FunctionDef) -> None:
                return

            visit_AsyncFunctionDef = visit_FunctionDef
            visit_ClassDef = visit_FunctionDef
            visit_Lambda = visit_FunctionDef

            def _visit_comprehension(self, child: ast.AST) -> None:
                generators = getattr(child, "generators", ())
                for generator in generators:
                    self.visit(generator.iter)
                    for condition in generator.ifs:
                        self.visit(condition)
                for field in ("elt", "key", "value"):
                    value = getattr(child, field, None)
                    if value is not None:
                        self.visit(value)

            visit_ListComp = _visit_comprehension
            visit_SetComp = _visit_comprehension
            visit_DictComp = _visit_comprehension
            visit_GeneratorExp = _visit_comprehension

        collector = LocalStoreCollector()
        for statement in node.body:
            collector.visit(statement)
        return collector.names

    def _is_process_marker(self, resolved: str | None) -> bool:
        return bool(
            resolved in _PROCESS_MODULES
            or resolved in _PROCESS_CALLABLES
            or resolved in _DYNAMIC_PROCESS_CALLABLES
            or (
                resolved is not None
                and resolved.rsplit(".", 1)[-1].casefold() in _PROCESS_TERMINALS
            )
        )

    def _record_process_carrier(self, node: ast.AST, resolved: str | None) -> None:
        if resolved in _PROCESS_CALLABLES or resolved in _DYNAMIC_PROCESS_CALLABLES:
            self._record(node, "process_callable_alias")
        elif (
            resolved is not None
            and resolved.rsplit(".", 1)[-1].casefold() in _PROCESS_TERMINALS
        ):
            self._record(node, "process_callable_alias")

    def _bind_target(self, target: ast.AST, value: ast.AST | None) -> None:
        if isinstance(target, (ast.Tuple, ast.List)):
            values = list(value.elts) if isinstance(value, (ast.Tuple, ast.List)) else []
            for index, child in enumerate(target.elts):
                self._bind_target(child, values[index] if index < len(values) else None)
            return
        if not isinstance(target, ast.Name):
            return
        resolved = self._resolve(value) if value is not None else None
        if self._is_process_marker(resolved):
            self.aliases[target.id] = resolved or _SHADOWED_PROCESS_NAME
            self._record_process_carrier(value or target, resolved)
        else:
            self.aliases[target.id] = _SHADOWED_PROCESS_NAME

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        self.class_stack.append(node.name)
        self.alias_scopes.append(dict(self.aliases))
        for statement in node.body:
            self.visit(statement)
        self.alias_scopes.pop()
        self.class_stack.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        defaults = list(node.args.defaults) + [
            default for default in node.args.kw_defaults if default is not None
        ]
        for default in defaults:
            resolved = self._resolve(default)
            if self._is_process_marker(resolved):
                self._record(default, "process_callable_default")
            self.visit(default)
        self.function_stack.append(node.name)
        self.alias_scopes.append(dict(self.aliases))
        local_names = self._argument_names(node.args) | self._function_local_names(node)
        for name in local_names:
            self.aliases[name] = _SHADOWED_PROCESS_NAME
        for statement in node.body:
            self.visit(statement)
        self.alias_scopes.pop()
        self.function_stack.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.function_stack.append("<lambda>")
        self.alias_scopes.append(dict(self.aliases))
        self.generic_visit(node)
        self.alias_scopes.pop()
        self.function_stack.pop()

    def _assignment_targets(self, node: ast.Assign | ast.AnnAssign) -> list[ast.expr]:
        return list(node.targets) if isinstance(node, ast.Assign) else [node.target]

    def _visit_assignment(self, node: ast.Assign | ast.AnnAssign) -> None:
        value = node.value
        targets = self._assignment_targets(node)
        if (
            self.relative_path == "dayz_mcp/native_launcher_backend.py"
            and self.function == "<module>"
            and any(isinstance(target, ast.Name) and target.id == "CREATE_FLAGS" for target in targets)
        ):
            required = {
                "CREATE_UNICODE_ENVIRONMENT",
                "DEBUG_PROCESS",
                "EXTENDED_STARTUPINFO_PRESENT",
            }
            names = self._creation_flag_names(value) if value is not None else None
            if names is not None and len(names) == 3 and set(names) == required:
                self.canonical_create_flags_definitions += 1
            else:
                self._record(node, "create_flags_contract_invalid")
        for target in targets:
            self._bind_target(target, value)
        self.generic_visit(node)

    visit_Assign = _visit_assignment
    visit_AnnAssign = _visit_assignment

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self._bind_target(node.target, node.value)
        self.visit(node.value)

    def visit_Call(self, node: ast.Call) -> None:
        resolved = self._resolve(node.func)
        terminal = (
            resolved.rsplit(".", 1)[-1].casefold()
            if resolved
            else node.func.attr.casefold()
            if isinstance(node.func, ast.Attribute)
            else ""
        )
        if terminal in _PROCESS_TERMINALS:
            if (
                terminal == "createprocessw"
                and isinstance(node.func, ast.Attribute)
                and resolved is not None
            ):
                self.direct_create_process_calls += 1
                if not (
                    self.relative_path == "dayz_mcp/native_launcher_backend.py"
                    and self.function == "_create_registered_launcher"
                    and self.direct_create_process_calls == 1
                    and self._is_canonical_create_process_call(node)
                ):
                    self._record(node, "create_process_wrong_boundary")
            else:
                self._record(node, "forbidden_windows_process_surface")
        elif resolved in _PROCESS_CALLABLES or (
            resolved is not None
            and resolved.startswith("os.spawn")
        ):
            self._record(node, "forbidden_python_process_surface")
        elif resolved in _DYNAMIC_PROCESS_CALLABLES and not (
            resolved in {"getattr", "builtins.getattr"}
            and not self._is_dynamic_process_lookup(node)
        ):
            kind = (
                "dynamic_process_lookup"
                if resolved in {"getattr", "builtins.getattr"}
                else "dynamic_process_import"
                if resolved in {"__import__", "builtins.__import__", "importlib.import_module"}
                else "dynamic_code_execution"
            )
            self._record(node, kind)
        elif isinstance(node.func, ast.Subscript):
            owner = self._resolve(node.func.value)
            if owner is not None and (
                owner.startswith("ctypes.") or owner.startswith("_winapi")
            ):
                self._record(node, "dynamic_process_lookup")
        self.generic_visit(node)


def audit_process_creation(tools_dir: Path) -> tuple[ProcessCreationFinding, ...]:
    tools_dir = Path(tools_dir)
    findings: list[ProcessCreationFinding] = []
    package = tools_dir / "dayz_mcp"
    sources = list(package.rglob("*.py")) if package.is_dir() else []
    modules = {_module_name(tools_dir, path): path for path in sources}
    available = frozenset(modules)
    pending = [
        _module_name(tools_dir, tools_dir / PurePosixPath(relative))
        for relative in sorted(_PROCESS_AUDIT_PATHS)
        if (tools_dir / PurePosixPath(relative)).is_file()
    ]
    selected: set[str] = set()
    parsed: dict[str, ast.Module] = {}
    while pending:
        module = pending.pop()
        if module in selected or module not in modules:
            continue
        selected.add(module)
        path = modules[module]
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError):
            relative = path.relative_to(tools_dir).as_posix()
            findings.append(ProcessCreationFinding(relative, 0, "<module>", "parse_error"))
            continue
        parsed[module] = tree
        pending.extend(_imported_modules(tree, module, available) - selected)
    for module in sorted(selected):
        if module not in parsed:
            continue
        path = modules[module]
        relative = path.relative_to(tools_dir).as_posix()
        visitor = _ProcessCreationVisitor(relative)
        visitor.visit(parsed[module])
        findings.extend(visitor.findings)
        if (
            relative == "dayz_mcp/native_launcher_backend.py"
            and visitor.direct_create_process_calls == 0
        ):
            findings.append(
                ProcessCreationFinding(
                    relative,
                    0,
                    "_create_registered_launcher",
                    "create_process_call_missing",
                )
            )
        if (
            relative == "dayz_mcp/native_launcher_backend.py"
            and visitor.direct_create_process_calls == 1
            and visitor.canonical_create_flags_definitions != 1
        ):
            findings.append(
                ProcessCreationFinding(
                    relative,
                    0,
                    "_create_registered_launcher",
                    "create_flags_contract_invalid",
                )
            )
    return tuple(
        sorted(
            findings,
            key=lambda finding: (
                finding.relative_path.casefold(),
                finding.line,
                finding.function,
                finding.kind,
            ),
        )
    )

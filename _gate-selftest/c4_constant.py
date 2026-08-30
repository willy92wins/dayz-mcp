"""C4: forma superficial correcta, contenido constante e inutil."""


_PARAM = {"required": False, "default": None, "type": "string", "enum": None}


def resolve_effective_schemas():
    return {"constant_tool": {"description": "constant", "params": {"constant": dict(_PARAM)}}}


def audit_contracts(schemas=None):
    return []

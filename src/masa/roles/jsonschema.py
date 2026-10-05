"""最小的 JSON Schema 校验器（无第三方依赖）：只支持角色输出契约需要的关键字。
A minimal JSON Schema validator (no third-party dependency): only the keywords role output contracts need.

为什么不用 jsonschema 库：项目核心保持 dependencies=[]；角色输出契约只需要少数关键字，自己写 60 行比引入依赖更可控。
Why not the jsonschema package: the core keeps dependencies=[]; role contracts need a handful of keywords, so 60 lines of our own are easier to control than a dependency.

支持 / supported: type, enum, const, properties, required, additionalProperties(bool|schema), items, minItems, maxItems, minLength, maxLength, minimum, maximum, oneOf.
"""


class SchemaError(ValueError):
    """值不符合 schema；message 带路径，便于作为“被拒绝的原因”回给模型。 The value violates the schema; the message carries the path so it can be fed back to the model as the rejection reason."""


_TYPES = {
    'object': lambda v: isinstance(v, dict),
    'array': lambda v: isinstance(v, list),
    'string': lambda v: isinstance(v, str),
    'boolean': lambda v: isinstance(v, bool),
    'integer': lambda v: isinstance(v, int) and not isinstance(v, bool),  # bool 是 int 的子类，必须排除 / bool subclasses int
    'number': lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    'null': lambda v: v is None,
}


def validate(value, schema, path='$'):
    """通过返回 None，不通过抛 SchemaError。 Returns None when valid, raises SchemaError otherwise."""
    if not isinstance(schema, dict):
        return
    if 'oneOf' in schema:
        matches = 0
        for option in schema['oneOf']:
            try:
                validate(value, option, path)
                matches += 1
            except SchemaError:
                pass
        if matches != 1:
            raise SchemaError(f'{path}: must match exactly one of the allowed shapes')
    if 'type' in schema:
        allowed = schema['type'] if isinstance(schema['type'], list) else [schema['type']]
        if not any(_TYPES[t](value) for t in allowed):
            raise SchemaError(f"{path}: expected {'/'.join(allowed)}")
    if 'const' in schema and value != schema['const']:
        raise SchemaError(f"{path}: must equal {schema['const']!r}")
    if 'enum' in schema and value not in schema['enum']:
        raise SchemaError(f"{path}: must be one of {schema['enum']!r}")
    if isinstance(value, str):
        if 'minLength' in schema and len(value) < schema['minLength']:
            raise SchemaError(f"{path}: shorter than {schema['minLength']}")
        if 'maxLength' in schema and len(value) > schema['maxLength']:
            raise SchemaError(f"{path}: longer than {schema['maxLength']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if 'minimum' in schema and value < schema['minimum']:
            raise SchemaError(f"{path}: below {schema['minimum']}")
        if 'maximum' in schema and value > schema['maximum']:
            raise SchemaError(f"{path}: above {schema['maximum']}")
    if isinstance(value, list):
        if 'minItems' in schema and len(value) < schema['minItems']:
            raise SchemaError(f"{path}: fewer than {schema['minItems']} items")
        if 'maxItems' in schema and len(value) > schema['maxItems']:
            raise SchemaError(f"{path}: more than {schema['maxItems']} items")
        if 'items' in schema:
            for index, item in enumerate(value):
                validate(item, schema['items'], f'{path}[{index}]')
    if isinstance(value, dict):
        properties = schema.get('properties', {})
        for name in schema.get('required', []):
            if name not in value:
                raise SchemaError(f'{path}: missing required field {name!r}')
        extra = schema.get('additionalProperties', True)
        for name, item in value.items():
            if name in properties:
                validate(item, properties[name], f'{path}.{name}')
            elif extra is False:
                raise SchemaError(f'{path}: unexpected field {name!r}')
            elif isinstance(extra, dict):
                validate(item, extra, f'{path}.{name}')

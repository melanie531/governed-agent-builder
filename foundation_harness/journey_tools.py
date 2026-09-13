"""Model schemas and argument limits pinned by the platform's AI Catalog."""
import copy
import math

from jsonschema import Draft202012Validator


def model_schema(tool):
    schema = copy.deepcopy(tool["inputSchema"])
    controls = tool.get("argument_controls", {})
    if not isinstance(controls, dict) or set(controls) - {"fixed", "maximums"}:
        raise ValueError("Catalog tool argument controls are invalid")
    fixed, maximums = controls.get("fixed", {}), controls.get("maximums", {})
    if not isinstance(fixed, dict) or not isinstance(maximums, dict) or set(fixed) & set(maximums):
        raise ValueError("Catalog tool argument controls are invalid")
    properties = schema.get("properties", {})
    for name, value in {**fixed, **maximums}.items():
        if name not in properties or not Draft202012Validator(properties[name]).is_valid(value):
            raise ValueError("Catalog tool argument control does not match the Gateway schema")
    for name, value in fixed.items():
        properties[name].update(enum=[value], default=value)
    for name, value in maximums.items():
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError("Catalog tool argument maximum must be a positive number")
        properties[name]["maximum"] = min(properties[name].get("maximum", value), value)
        properties[name]["default"] = value
    return schema


def tool_arguments(tool, arguments):
    if not isinstance(arguments, dict):
        raise ValueError("Model requested invalid tool arguments")
    schema = model_schema(tool)
    controls = tool.get("argument_controls", {})
    result = copy.deepcopy(arguments)
    result.update(copy.deepcopy(controls.get("fixed", {})))
    for name, limit in controls.get("maximums", {}).items():
        value = result.get(name, limit)
        if type(value) in (int, float):
            result[name] = min(value, limit)
    if not Draft202012Validator(schema).is_valid(result):
        raise ValueError("Model requested an unselected tool or invalid arguments")
    return result

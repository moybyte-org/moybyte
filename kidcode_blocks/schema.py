"""Small blocks.json validator."""

from kidcode.errors import ManifestError

BLOCK_SCHEMA = "kidcode.blocks.v1"


def validate_blocks(data):
    if not isinstance(data, dict):
        raise ManifestError("blocks file must be an object")
    if data.get("schema") != BLOCK_SCHEMA:
        raise ManifestError("blocks.schema must be " + BLOCK_SCHEMA)
    for name in ["variables", "sprites", "scripts"]:
        if name in data and not isinstance(data[name], list):
            raise ManifestError("blocks." + name + " must be a list")
    return data

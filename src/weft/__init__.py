"""
Weft: schemas, versioned migrations, and data pipelines.

A migration must account for every field that differs between two schema
versions, must declare itself lossy if it cannot be reversed, and must not
weaken a field's data classification. Reversible migrations get both directions
generated and an `invertible_by` law attached, so the round trip is checked by
the verifier rather than asserted.

Pipelines lower to functions plus a derived lineage record: which fields flowed
where, and the highest classification that passed through.
"""

__version__ = "0.1.0"

from .lang import (  # noqa: E402
    LANGUAGE_VERSION,
    FieldDecl,
    Lowering,
    MigrationDecl,
    PipelineDecl,
    SchemaDecl,
    StageDecl,
    WeftParser,
    parse_weft,
)

__all__ = [
    "__version__", "LANGUAGE_VERSION",
    "parse_weft", "WeftParser", "Lowering",
    "SchemaDecl", "FieldDecl", "MigrationDecl", "PipelineDecl", "StageDecl",
]

"""
weft, schemas and versioned migrations and data pipelines.

a migration has to account for every field that differs between two versions,
has to say `lossy` if it cannot be reversed, and cannot weaken a field's
classification. reversible ones get both directions generated with an
`invertible_by` law on them so the verifier runs the round trip instead of
somebody asserting it.

pipelines lower to a function plus a lineage record, which fields went where
and the highest classification that passed through.
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

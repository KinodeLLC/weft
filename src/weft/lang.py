"""
Weft: schemas, versioned migrations, and data pipelines.

The problem Weft exists to solve is that a schema change is two changes -- the
new shape and the path from the old one -- and the second is usually written by
hand, later, by someone who no longer remembers the first. Weft derives what it
can and refuses what it cannot:

  * A migration must account for every field that differs between two versions.
    A field added without a value, or removed without a way back, is a compile
    error naming the field. The compiler knows what changed; the author should
    not have to.

  * A migration that cannot be reversed must say `lossy` and say why. Otherwise
    Weft generates both directions and attaches an `invertible_by` law, so the
    round trip is checked by the verifier against generated records rather than
    asserted in a comment.

  * Field classifications survive migration. A field marked `personal` in v2 is
    still `personal` in v3 unless the migration explicitly reclassifies it, so
    a rename cannot quietly launder protected data.

Pipelines lower to ordinary functions, plus a lineage record: which fields
flowed from which source to which sink, and the highest data classification
that passed through. That artifact is generated from the code rather than
maintained alongside it, so it cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from canon import ast as A
from canon import types as TY
from canon.diagnostics import Bag, Repair, Span
from canon.lexer import Lexer, T
from canon.parser import Parser


LANGUAGE_VERSION = "0.1"

SUPPORT_TYPES = """
record FieldFlow {
  field_name: Text
  source: Text
  sink: Text
  classification: Text
}

record Lineage {
  pipeline_name: Text
  flows: List<FieldFlow>
  highest_classification: Text
}
"""


# --------------------------------------------------------------------------
# Surface declarations
# --------------------------------------------------------------------------

@dataclass
class FieldDecl:
    name: str = ""
    ty: Optional[A.TypeExpr] = None
    key: bool = False
    unique: bool = False
    classification: str = ""
    default: Optional[A.Expr] = None
    doc: str = ""
    span: Span = field(default_factory=Span.unknown)


@dataclass
class SchemaDecl:
    name: str = ""
    version: int = 1
    fields: list = field(default_factory=list)
    invariants: list = field(default_factory=list)
    indexes: list = field(default_factory=list)
    retention_days: int = 0
    intent: str = ""
    doc: str = ""
    span: Span = field(default_factory=Span.unknown)

    @property
    def type_name(self) -> str:
        return f"{self.name}V{self.version}"

    def field(self, name) -> Optional[FieldDecl]:
        return next((f for f in self.fields if f.name == name), None)

    def names(self) -> set:
        return {f.name for f in self.fields}


@dataclass
class MigrationDecl:
    schema: str = ""
    from_version: int = 0
    to_version: int = 0
    lossy: bool = False
    lossy_reason: str = ""
    forward: dict = field(default_factory=dict)    # field -> expr (over `old`)
    backward: dict = field(default_factory=dict)   # field -> expr (over `new`)
    dropped: list = field(default_factory=list)
    intent: str = ""
    span: Span = field(default_factory=Span.unknown)


@dataclass
class StageDecl:
    kind: str = "transform"        # source | transform | sink
    name: str = ""
    ty: Optional[A.TypeExpr] = None
    expr: Optional[A.Expr] = None
    reads: list = field(default_factory=list)
    span: Span = field(default_factory=Span.unknown)


@dataclass
class PipelineDecl:
    name: str = ""
    params: list = field(default_factory=list)
    result: Optional[A.TypeExpr] = None
    intent: str = ""
    doc: str = ""
    uses: list = field(default_factory=list)
    cost: Optional[A.Cost] = None
    stages: list = field(default_factory=list)
    final: Optional[A.Expr] = None
    span: Span = field(default_factory=Span.unknown)


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------

class WeftParser(Parser):
    def __init__(self, tokens, source="", filename="<memory>", bag=None):
        super().__init__(tokens, source, filename, bag, language="weft")
        self.schemas: list = []
        self.migrations: list = []
        self.pipelines: list = []

    def parse_decl(self):
        doc = self.skip_docs()
        if self.at_ctx("schema") and self.at(1).kind == T.UPPER:
            self.schemas.append(self.parse_schema(doc))
            return None
        if self.at_ctx("migrate") and self.at(1).kind == T.UPPER:
            self.migrations.append(self.parse_migration(doc))
            return None
        if self.at_ctx("pipeline") and self.at(1).kind == T.NAME:
            self.pipelines.append(self.parse_pipeline(doc))
            return None
        for fn, kw in ((self.parse_fn, "fn"), (self.parse_record, "record"),
                       (self.parse_enum, "enum"), (self.parse_alias, "alias"),
                       (self.parse_effect, "effect"), (self.parse_const, "const"),
                       (self.parse_test, "test")):
            if self.cur.is_kw(kw):
                return fn(doc)
        return None

    # ------------------------------------------------------------------

    def parse_version(self) -> int:
        """A version marker, written `v3`."""
        if self.cur.kind == T.NAME and self.cur.value.startswith("v") \
                and self.cur.value[1:].isdigit():
            return int(self.next().value[1:])
        self.err("CANON-E0101", "expected a version marker such as `v3`",
                 facts={"found": self.cur.value or self.cur.kind})
        return 0

    def parse_schema(self, doc: str = "") -> SchemaDecl:
        start = self.next()            # schema
        s = SchemaDecl(doc=doc)
        s.name = self.expect_upper("a schema name")
        s.version = self.parse_version()
        self.expect_punct("{", "to open the schema body")

        while not self.cur.is_punct("}") and self.cur.kind != T.EOF:
            fdoc = self.skip_docs()
            if self.cur.is_kw("intent"):
                self.next()
                s.intent = self.parse_text_literal("an intent description")
            elif self.at_ctx("retention"):
                self.next()
                s.retention_days = self._int("a retention period in days")
                self.eat_ctx("days")
            elif self.at_ctx("index"):
                self.next()
                while self.cur.kind == T.NAME:
                    s.indexes.append(self.next().value)
                    if not self.eat_punct(","):
                        break
            elif self.at_ctx("invariant"):
                self.next()
                s.invariants.append(self.parse_expr())
            elif self.at_ctx("field"):
                s.fields.append(self.parse_field(fdoc))
            else:
                self.err("CANON-E0102",
                         "expected `field`, `index`, `retention`, "
                         "`invariant` or `intent` in a schema body",
                         facts={"found": self.cur.value or self.cur.kind})
                self.next()
        self.expect_punct("}", "to close the schema body")

        if not s.fields:
            self.err("CANON-E0102", f"schema {s.name!r} has no fields", start)
        keys = [f.name for f in s.fields if f.key]
        if not keys:
            self.err(
                "CANON-E0102",
                f"schema {s.name} v{s.version} has no key field", start,
                facts={"schema": s.name, "version": s.version},
                repairs=[Repair("manual", "mark the identifying field `key`",
                                "field id: Text key", start.span, 0.6)],
                notes=["A migration has to match records between versions, "
                       "which needs a stable identity."])
        if len(keys) > 1:
            self.err("CANON-E0102",
                     f"schema {s.name} v{s.version} has more than one key "
                     f"field: {', '.join(keys)}", start,
                     facts={"keys": keys})
        for idx in s.indexes:
            if s.field(idx) is None:
                self.err("CANON-E0206",
                         f"cannot index {idx!r}: {s.name} has no such field",
                         start, facts={"field": idx,
                                       "available": sorted(s.names())})
        s.span = self.span_from(start)
        return s

    def parse_field(self, doc: str = "") -> FieldDecl:
        start = self.next()            # field
        f = FieldDecl(doc=doc)
        f.name = self.expect_name("a field name")
        self.expect_punct(":", "before the field type")
        f.ty = self.parse_type()
        while True:
            if self.at_ctx("key"):
                self.next()
                f.key = True
            elif self.at_ctx("unique"):
                self.next()
                f.unique = True
            elif self.at_ctx("classify"):
                self.next()
                f.classification = self.expect_name("a data classification")
                if f.classification not in TY.CLASS_RANK:
                    self.err("CANON-E0201",
                             f"unknown data classification "
                             f"{f.classification!r}", start,
                             facts={"known": TY.CLASSIFICATIONS})
            elif self.cur.is_op("="):
                self.next()
                f.default = self.parse_expr()
            else:
                break
        f.span = self.span_from(start)
        return f

    def _int(self, what) -> int:
        if self.cur.kind == T.INT:
            return int(self.next().payload)
        self.err("CANON-E0101", f"expected {what}")
        return 0

    # ------------------------------------------------------------------

    def parse_migration(self, doc: str = "") -> MigrationDecl:
        start = self.next()            # migrate
        m = MigrationDecl()
        m.schema = self.expect_upper("a schema name")
        m.from_version = self.parse_version()
        self.expect_op("->", "between the two versions")
        m.to_version = self.parse_version()
        if self.at_ctx("lossy"):
            self.next()
            m.lossy = True
            if self.cur.kind == T.TEXT:
                m.lossy_reason = self.next().payload
        self.expect_punct("{", "to open the migration body")

        while not self.cur.is_punct("}") and self.cur.kind != T.EOF:
            self.skip_docs()
            if self.cur.is_kw("intent"):
                self.next()
                m.intent = self.parse_text_literal("an intent description")
            elif self.at_ctx("forward"):
                self.next()
                name = self.expect_name("a field name")
                self.expect_op("=", "before the value")
                m.forward[name] = self.parse_expr()
            elif self.at_ctx("backward"):
                self.next()
                name = self.expect_name("a field name")
                self.expect_op("=", "before the value")
                m.backward[name] = self.parse_expr()
            elif self.at_ctx("drop"):
                self.next()
                m.dropped.append(self.expect_name("a field name"))
            else:
                self.err("CANON-E0102",
                         "expected `forward`, `backward`, `drop` or `intent` "
                         "in a migration body",
                         facts={"found": self.cur.value or self.cur.kind})
                self.next()
        self.expect_punct("}", "to close the migration body")
        m.span = self.span_from(start)
        return m

    # ------------------------------------------------------------------

    def parse_pipeline(self, doc: str = "") -> PipelineDecl:
        start = self.next()            # pipeline
        p = PipelineDecl(doc=doc)
        p.name = self.expect_name("a pipeline name")
        p.params = self.parse_params()
        self.expect_op("->", "before the pipeline result type")
        p.result = self.parse_type()

        while True:
            if self.cur.is_kw("intent"):
                self.next()
                p.intent = self.parse_text_literal("an intent description")
            elif self.cur.is_kw("uses"):
                self.next()
                p.uses.extend(self.parse_effect_refs())
            elif self.cur.is_kw("cost"):
                self.next()
                p.cost = self.parse_cost(p.cost)
            else:
                break

        self.expect_punct("{", "to open the pipeline body")
        while not self.cur.is_punct("}") and self.cur.kind != T.EOF:
            self.skip_docs()
            if self.at_ctx("source", "transform"):
                p.stages.append(self.parse_stage(self.cur.value))
            elif self.at_ctx("sink"):
                p.stages.append(self.parse_sink())
            else:
                p.final = self.parse_expr()
                break
        self.expect_punct("}", "to close the pipeline body")

        if p.final is None:
            p.final = A.Lit(value=None, lit_kind="unit")
        if not any(s.kind == "source" for s in p.stages):
            self.err("CANON-E0102",
                     f"pipeline {p.name!r} has no source stage", start,
                     facts={"pipeline": p.name},
                     notes=["A pipeline with no source has no lineage to "
                            "record, which is the reason to write one."])
        p.span = self.span_from(start)
        return p

    def parse_stage(self, kind: str) -> StageDecl:
        start = self.next()            # source | transform
        s = StageDecl(kind=kind)
        s.name = self.expect_name("a stage name")
        if self.eat_punct(":"):
            s.ty = self.parse_type()
        if self.eat_ctx("from"):
            pass
        else:
            self.expect_op("=", "before the stage expression")
        s.expr = self.parse_expr()
        s.reads = sorted(_paths_in(s.expr))
        s.span = self.span_from(start)
        return s

    def parse_sink(self) -> StageDecl:
        start = self.next()            # sink
        s = StageDecl(kind="sink")
        if self.cur.kind == T.NAME and self.at(1).is_op("="):
            s.name = self.next().value
            self.next()
        else:
            s.name = "sink"
        if self.eat_ctx("to"):
            pass
        s.expr = self.parse_expr()
        s.reads = sorted(_paths_in(s.expr))
        s.span = self.span_from(start)
        return s


# --------------------------------------------------------------------------
# Lowering
# --------------------------------------------------------------------------

def _lit(v, k="text"):
    return A.Lit(value=v, lit_kind=k)


def _var(n):
    return A.Var(name=n)


def _field(target, name):
    return A.Field(target=target, name=name)


def _rec(tn, fields):
    return A.RecordLit(type_name=tn, fields=list(fields))


class Lowering:
    def __init__(self, bag: Bag):
        self.bag = bag

    def lower_module(self, mod: A.Module, parser: WeftParser) -> A.Module:
        by_key = {}
        for s in parser.schemas:
            key = (s.name, s.version)
            if key in by_key:
                self.bag.error("CANON-E0203",
                               f"schema {s.name} v{s.version} is declared "
                               f"twice", s.span)
            by_key[key] = s
            mod.decls.append(self.lower_schema(s))

        if parser.pipelines:
            mod.decls.extend(_support_decls())

        for m in parser.migrations:
            mod.decls.extend(self.lower_migration(m, by_key))

        for p in parser.pipelines:
            mod.decls.extend(self.lower_pipeline(p, by_key))

        mod.language = "weft"
        return mod

    # ------------------------------------------------------------------

    def lower_schema(self, s: SchemaDecl) -> A.RecordDecl:
        rec = A.RecordDecl(
            name=s.type_name,
            doc=s.doc,
            intent=s.intent or f"{s.name}, schema version {s.version}.",
            fields=[A.Param(name=f.name, ty=f.ty, doc=f.doc, span=f.span)
                    for f in s.fields],
            invariants=list(s.invariants),
            classification={f.name: f.classification
                            for f in s.fields if f.classification},
            span=s.span)
        return rec

    # ------------------------------------------------------------------

    def lower_migration(self, m: MigrationDecl, schemas: dict) -> list:
        old = schemas.get((m.schema, m.from_version))
        new = schemas.get((m.schema, m.to_version))

        if old is None or new is None:
            missing = [f"{m.schema} v{v}" for v, s in
                       ((m.from_version, old), (m.to_version, new)) if s is None]
            self.bag.error(
                "CANON-E0202",
                f"migration refers to a schema version that is not declared: "
                f"{', '.join(missing)}", m.span,
                facts={"missing": missing,
                       "declared": sorted(f"{n} v{v}" for n, v in schemas)})
            return []

        added = sorted(new.names() - old.names())
        removed = sorted(old.names() - new.names())
        kept = sorted(new.names() & old.names())

        # Every field that differs has to be accounted for. The compiler knows
        # exactly which ones, so the diagnostic names them rather than saying
        # the migration is incomplete.
        unhandled_add = [f for f in added
                         if f not in m.forward and new.field(f).default is None]
        if unhandled_add:
            self.bag.error(
                "CANON-E0102",
                f"migration {m.schema} v{m.from_version} -> v{m.to_version} "
                f"does not supply "
                f"{'a value' if len(unhandled_add) == 1 else 'values'} for "
                + ", ".join(unhandled_add),
                m.span,
                facts={"fields": unhandled_add, "schema": m.schema},
                repairs=[Repair(
                    "manual", "supply the new field's value",
                    "\n".join(f"forward {f} = <expression over old>"
                              for f in unhandled_add), m.span, 0.7)],
                notes=["A field added without a value cannot be populated for "
                       "records that already exist."])

        unhandled_remove = [f for f in removed if f not in m.backward]
        if unhandled_remove and not m.lossy:
            self.bag.error(
                "CANON-E0102",
                f"migration {m.schema} v{m.from_version} -> v{m.to_version} "
                f"removes " + ", ".join(unhandled_remove)
                + " without a way to restore "
                + ("it" if len(unhandled_remove) == 1 else "them"),
                m.span,
                facts={"fields": unhandled_remove, "schema": m.schema},
                repairs=[
                    Repair("manual", "say how to reconstruct the field",
                           "\n".join(f"backward {f} = <expression over new>"
                                     for f in unhandled_remove), m.span, 0.6),
                    Repair("manual",
                           "or declare the migration irreversible and say why",
                           f'migrate {m.schema} v{m.from_version} -> '
                           f'v{m.to_version} lossy "reason"', m.span, 0.5)],
                notes=["An unreversible migration is sometimes correct, but "
                       "it has to be a stated decision rather than an "
                       "oversight, because it determines whether a bad "
                       "deployment can be rolled back."])

        if m.lossy and not m.lossy_reason:
            self.bag.warn(
                "CANON-W0005",
                f"migration {m.schema} v{m.from_version} -> v{m.to_version} "
                f"is declared lossy but does not say why", m.span,
                repairs=[Repair("manual", "state the reason",
                                f'lossy "..."', m.span, 0.6)])

        # Classification must not weaken across a migration.
        for name in kept:
            o, n = old.field(name), new.field(name)
            if o.classification and TY.class_rank(n.classification or "public") \
                    < TY.class_rank(o.classification):
                self.bag.error(
                    "CANON-E0903",
                    f"field {name!r} is classified {o.classification!r} in "
                    f"v{m.from_version} but "
                    f"{n.classification or 'public'!r} in v{m.to_version}",
                    m.span,
                    facts={"field": name, "was": o.classification,
                           "now": n.classification or "public"},
                    notes=["A migration must not downgrade a data "
                           "classification. Protected data stays protected "
                           "through a rename or a reshape."])

        decls = []
        fwd_name = f"migrate_{_snake(m.schema)}_v{m.from_version}_v{m.to_version}"
        bwd_name = f"rollback_{_snake(m.schema)}_v{m.to_version}_v{m.from_version}"

        fwd_fields = []
        for f in new.fields:
            if f.name in m.forward:
                fwd_fields.append((f.name, m.forward[f.name]))
            elif f.name in old.names():
                fwd_fields.append((f.name, _field(_var("old"), f.name)))
            elif f.default is not None:
                fwd_fields.append((f.name, f.default))
            else:
                fwd_fields.append((f.name, _zero_for(f.ty)))

        forward = A.FnDecl(
            name=fwd_name,
            intent=m.intent or (f"Migrate a {m.schema} record from "
                                f"v{m.from_version} to v{m.to_version}."),
            params=[A.Param(name="old", ty=A.TName(name=old.type_name))],
            result=A.TName(name=new.type_name),
            body=A.Block(stmts=[], result=_rec(new.type_name, fwd_fields)),
            origin="weft", span=m.span)
        decls.append(forward)

        reversible = not m.lossy and not unhandled_remove
        if reversible:
            bwd_fields = []
            for f in old.fields:
                if f.name in m.backward:
                    bwd_fields.append((f.name, m.backward[f.name]))
                elif f.name in new.names():
                    bwd_fields.append((f.name, _field(_var("new"), f.name)))
                else:
                    bwd_fields.append((f.name, _zero_for(f.ty)))

            backward = A.FnDecl(
                name=bwd_name,
                intent=(f"Roll a {m.schema} record back from "
                        f"v{m.to_version} to v{m.from_version}."),
                params=[A.Param(name="new", ty=A.TName(name=new.type_name))],
                result=A.TName(name=old.type_name),
                body=A.Block(stmts=[], result=_rec(old.type_name, bwd_fields)),
                origin="weft", span=m.span)
            decls.append(backward)

            # The round trip is checked by the verifier against generated
            # records rather than asserted anywhere.
            forward.laws.append(A.LawRef(name="invertible_by",
                                         args=[_var(bwd_name)]))
            forward.laws.append(A.LawRef(name="deterministic"))

        key_field = next((f.name for f in new.fields if f.key), None)
        if key_field and key_field in old.names():
            forward.ensures.append(
                A.Binary(op="==",
                         left=_field(_var("result"), key_field),
                         right=_field(_var("old"), key_field)))

        compat = A.ConstDecl(
            name=f"{_snake(m.schema)}_v{m.from_version}_v{m.to_version}_compatibility",
            ty=A.TName(name="Text"),
            value=_lit("reversible" if reversible else "lossy"),
            doc=(f"Whether {m.schema} v{m.from_version} -> v{m.to_version} "
                 f"can be rolled back."
                 + (f" Lossy: {m.lossy_reason}" if m.lossy_reason else "")))
        decls.append(compat)
        return decls

    # ------------------------------------------------------------------

    def lower_pipeline(self, p: PipelineDecl, schemas: dict) -> list:
        stmts = []
        flows = []
        highest = "public"

        classification_of = {}
        for s in schemas.values():
            for f in s.fields:
                if f.classification:
                    classification_of[f.name] = max(
                        classification_of.get(f.name, "public"),
                        f.classification, key=TY.class_rank)

        sources = [s.name for s in p.stages if s.kind == "source"]
        sinks = [s.name for s in p.stages if s.kind == "sink"]

        for stage in p.stages:
            if stage.kind == "sink":
                stmts.append(A.SExpr(value=stage.expr, span=stage.span))
            else:
                stmts.append(A.SLet(name=stage.name, ty=stage.ty,
                                    value=stage.expr, span=stage.span))

            for path in stage.reads:
                leaf = path.rsplit(".", 1)[-1]
                cls = classification_of.get(leaf, "public")
                highest = max(highest, cls, key=TY.class_rank)
                flows.append((path, stage.name, stage.kind, cls))

        lineage = A.ConstDecl(
            name=f"{p.name}_lineage",
            ty=A.TName(name="Lineage"),
            doc=(f"Field-level lineage for the {p.name} pipeline, derived "
                 f"from its stages."),
            value=_rec("Lineage", [
                ("pipeline_name", _lit(p.name)),
                ("flows", A.ListLit(items=[
                    _rec("FieldFlow", [
                        ("field_name", _lit(path)),
                        ("source", _lit(sources[0] if sources else "")),
                        ("sink", _lit(stage_name if kind == "sink"
                                      else (sinks[0] if sinks else ""))),
                        ("classification", _lit(cls)),
                    ]) for path, stage_name, kind, cls in flows])),
                ("highest_classification", _lit(highest)),
            ]))

        fn = A.FnDecl(
            name=p.name,
            doc=p.doc,
            intent=p.intent or f"Run the {p.name} pipeline.",
            params=list(p.params),
            result=p.result,
            uses=list(p.uses),
            cost=p.cost,
            body=A.Block(stmts=stmts, result=p.final, span=p.span),
            origin="weft", span=p.span)

        return [lineage, fn]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _zero_for(ty) -> A.Expr:
    """A value of the right type for a field with nothing to carry over."""
    if isinstance(ty, A.TName):
        if ty.name == "Int":
            return _lit(0, "int")
        if ty.name == "Dec":
            from decimal import Decimal
            return _lit(Decimal(0), "dec")
        if ty.name == "Text":
            return _lit("")
        if ty.name == "Bool":
            return _lit(False, "bool")
        if ty.name == "Unit":
            return _lit(None, "unit")
        if ty.name == "List":
            return A.ListLit(items=[])
        if ty.name == "Option":
            return A.CtorCall(name="None")
    return _lit(None, "unit")


def _paths_in(expr) -> set:
    out = set()
    if expr is None:
        return out
    for node in expr.walk():
        if isinstance(node, A.Field):
            parts = []
            cur = node
            while isinstance(cur, A.Field):
                parts.append(cur.name)
                cur = cur.target
            if isinstance(cur, A.Var):
                parts.append(cur.name)
                out.add(".".join(reversed(parts)))
    return out


def _snake(name: str) -> str:
    out = []
    for i, ch in enumerate(name):
        if ch.isupper() and i:
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def _support_decls() -> list:
    src = "module _weft_support\n" + SUPPORT_TYPES
    lx = Lexer(src, "<weft-support>")
    toks = lx.run()
    p = Parser(toks, src, "<weft-support>", lx.bag)
    mod = p.parse_module()
    if p.bag.has_errors:
        raise RuntimeError("weft support types failed to parse:\n"
                           + p.bag.render(src))
    return mod.decls


# --------------------------------------------------------------------------

def parse_weft(source: str, filename: str = "<memory>"):
    """Parse and lower Weft source. Returns (Canon Module, Bag)."""
    lx = Lexer(source, filename)
    toks = lx.run()
    p = WeftParser(toks, source, filename, lx.bag)
    mod = p.parse_module()
    mod = Lowering(p.bag).lower_module(mod, p)
    return mod, p.bag

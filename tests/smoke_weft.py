"""Weft: schema versioning, derived migrations, and lineage."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "canon" / "src"))
sys.path.insert(0, str(ROOT / "weft" / "src"))

from canon import Hasher, format_module  # noqa: E402
from canon import values as V  # noqa: E402
from canon.checker import check  # noqa: E402
from canon.interp import Budget, Interpreter  # noqa: E402
from canon.ledger import AuditLog, CapabilityBroker, Ledger  # noqa: E402
from canon.verifier import Verifier  # noqa: E402
from weft import parse_weft  # noqa: E402

SRC = r'''
module customers

effect store {
  scan(table: Text) -> List<Text>
  write(key: Text, value: Text) -> Unit
}

schema Customer v2 {
  intent "A customer as stored before the tier programme."
  field id: Text key
  field email: Text classify personal unique
  field display_name: Text classify personal
  field signup_millis: Int
  retention 2555 days
  index email
  invariant signup_millis >= 0
}

schema Customer v3 {
  intent "A customer, with the loyalty tier added."
  field id: Text key
  field email: Text classify personal unique
  field display_name: Text classify personal
  field signup_millis: Int
  field tier: Text = "standard"
  retention 2555 days
  index email
  invariant signup_millis >= 0
}

migrate Customer v2 -> v3 {
  intent "Add the loyalty tier, defaulting existing customers to standard."
  forward tier = "standard"
}

pipeline tier_report(rows: List<CustomerV3>) -> Int
  intent "Count customers on the standard tier and record the total."
  uses store.write
{
  source ids: List<Text> = List.map(rows, fn(c: CustomerV3) => c.id)
  transform standard = List.filter(rows, fn(c: CustomerV3) => c.tier == "standard")
  transform total: Int = List.length(standard)
  sink store.write("tier_report", Int.to_text(total))
  total
}
'''

# Adds a field with no value supplied and no default.
MISSING_VALUE = SRC.replace(
    '  field tier: Text = "standard"\n',
    "  field tier: Text\n  field segment: Text\n").replace(
    '  forward tier = "standard"\n', '  forward tier = "standard"\n')

# Removes a field without a way to restore it, and without declaring lossy.
IRREVERSIBLE = SRC.replace(
    "  field display_name: Text classify personal\n  field signup_millis: Int\n"
    '  field tier: Text = "standard"\n',
    "  field signup_millis: Int\n  field tier: Text = \"standard\"\n")

# Same removal, declared lossy with a reason.
LOSSY = IRREVERSIBLE.replace(
    "migrate Customer v2 -> v3 {",
    'migrate Customer v2 -> v3 lossy "display names were merged into the '
    'identity service and are no longer stored here" {')

# Weakens a classification across the migration.
DOWNGRADE = SRC.replace(
    "  field email: Text classify personal unique\n  field display_name: Text classify personal\n"
    "  field signup_millis: Int\n  field tier",
    "  field email: Text classify personal unique\n  field display_name: Text classify public\n"
    "  field signup_millis: Int\n  field tier")

NO_KEY = SRC.replace("  field id: Text key\n  field email: Text classify personal unique\n"
                     "  field display_name: Text classify personal\n"
                     "  field signup_millis: Int\n  field tier",
                     "  field id: Text\n  field email: Text classify personal unique\n"
                     "  field display_name: Text classify personal\n"
                     "  field signup_millis: Int\n  field tier")


def build(src):
    mod, bag = parse_weft(src, "customers.weft")
    if bag.has_errors:
        return None, bag, mod
    res = check([mod], bag)
    return res, res.bag, mod


def v2(cid="c-1", email="a@example.com", name="Ada", signup=1_700_000_000_000):
    return V.Record("CustomerV2", (("id", cid), ("email", email),
                                   ("display_name", name),
                                   ("signup_millis", signup)))


def main():
    res, bag, mod = build(SRC)
    if res is None or bag.has_errors:
        print(bag.render(SRC))
        return 1
    errs = [d for d in bag if d.severity.value == "error"]
    if errs:
        for d in errs:
            print(d.render(SRC))
        return 1

    failures = []

    def case(name, fn):
        try:
            print(f"  ok    {name}: {fn()}")
        except AssertionError as ae:
            failures.append(name)
            print(f"  FAIL  {name}: {ae}")

    def led():
        audit = AuditLog(actor="weft")
        broker = CapabilityBroker(audit=audit)
        broker.grant("weft", ["*"], reason="smoke test")
        l = Ledger(broker=broker, audit=audit, actor="weft")
        l.handle("store.write", lambda k, v: V.UNIT)
        l.handle("store.scan", lambda t: ())
        return l

    print("schemas")

    def t_schema_records():
        types = sorted(res.env.types)
        assert "CustomerV2" in types and "CustomerV3" in types, types
        ti = res.env.types["CustomerV3"]
        assert ti.decl.classification.get("email") == "personal", \
            ti.decl.classification
        assert ti.decl.invariants, "the schema invariant was dropped"
        return (f"CustomerV2/V3 with classifications "
                f"{sorted(ti.decl.classification)}")
    case("each schema version becomes its own record type", t_schema_records)

    def t_key_required():
        _, b, _ = build(NO_KEY)
        assert b.has_errors, "a schema without a key compiled"
        d = next(x for x in b if "no key field" in x.message)
        return d.message
    case("a schema must have exactly one key field", t_key_required)

    print("\nmigrations")

    def t_generated():
        names = sorted(res.env.fns)
        assert "customers.migrate_customer_v2_v3" in names, names
        assert "customers.rollback_customer_v3_v2" in names, names
        assert "customers.customer_v2_v3_compatibility" in res.env.consts
        return "forward, backward and a compatibility marker were generated"
    case("a reversible migration generates both directions", t_generated)

    def t_forward():
        it = Interpreter(res, led(), Budget())
        out = it.call("migrate_customer_v2_v3", [v2()])
        assert out.type_name == "CustomerV3", out.type_name
        assert out.get("tier") == "standard", out.get("tier")
        assert out.get("email") == "a@example.com"
        return f"v2 -> v3 with tier={out.get('tier')!r}"
    case("the forward migration fills the new field", t_forward)

    def t_round_trip():
        it = Interpreter(res, led(), Budget())
        original = v2()
        forward = it.call("migrate_customer_v2_v3", [original])
        back = it.call("rollback_customer_v3_v2", [forward])
        assert V.compare(original, back) == 0, \
            f"{V.show(original)} != {V.show(back)}"
        return "v2 -> v3 -> v2 recovers the original record"
    case("a reversible migration round-trips", t_round_trip)

    def t_law_checked():
        fi = res.env.fns["customers.migrate_customer_v2_v3"]
        laws = [l.name for l in fi.decl.laws]
        assert "invertible_by" in laws, laws
        hashes = {qn: di.hash
                  for qn, di in Hasher().add_modules(res.modules).items()}
        v = Verifier(res, seed="kinode", runs=40, hashes=hashes)
        rep = v.verify_all(only=["customers.migrate_customer_v2_v3"])
        f = rep.functions[0]
        assert f.ok, [c.to_json() for c in f.counterexamples]
        return (f"invertible_by verified over {f.runs} generated records")
    case("the round trip is checked by the verifier, not asserted",
         t_law_checked)

    def t_missing_value():
        _, b, _ = build(MISSING_VALUE)
        assert b.has_errors, "an unpopulated new field compiled"
        d = next(x for x in b if "does not supply" in x.message)
        assert "segment" in d.facts.get("fields", []), d.facts
        return d.message
    case("a new field with no value and no default is rejected",
         t_missing_value)

    def t_irreversible():
        _, b, _ = build(IRREVERSIBLE)
        assert b.has_errors, "an irreversible migration compiled silently"
        d = next(x for x in b if "without a way to restore" in x.message)
        assert "display_name" in d.facts.get("fields", []), d.facts
        return d.message
    case("removing a field with no way back is rejected", t_irreversible)

    def t_lossy_declared():
        r, b, _ = build(LOSSY)
        assert r is not None and not b.has_errors, b.render(LOSSY)
        names = sorted(r.env.fns)
        assert "customers.rollback_customer_v3_v2" not in names, \
            "a lossy migration generated a rollback it cannot honour"
        ci = r.env.consts["customers.customer_v2_v3_compatibility"]
        it = Interpreter(r, led(), Budget())
        value = it.eval(ci.decl.value, it.globals)
        assert value == "lossy", value
        return "declared lossy: no rollback generated, marked lossy"
    case("a migration declared lossy compiles and is marked as such",
         t_lossy_declared)

    def t_no_downgrade():
        _, b, _ = build(DOWNGRADE)
        assert b.has_errors, "a classification downgrade compiled"
        d = next(x for x in b if x.code == "CANON-E0903")
        assert d.facts.get("field") == "display_name", d.facts
        return d.message
    case("a migration cannot weaken a data classification", t_no_downgrade)

    print("\npipelines")

    def t_pipeline_runs():
        rows = tuple(
            V.Record("CustomerV3", (("id", f"c-{i}"), ("email", f"{i}@x.com"),
                                    ("display_name", f"n{i}"),
                                    ("signup_millis", 1), ("tier", tier)))
            for i, tier in enumerate(["standard", "gold", "standard"]))
        it = Interpreter(res, led(), Budget())
        total = it.call("tier_report", [rows])
        assert total == 2, total
        return f"counted {total} standard-tier customers"
    case("a pipeline lowers to a function that runs", t_pipeline_runs)

    def t_lineage():
        ci = res.env.consts["customers.tier_report_lineage"]
        it = Interpreter(res, led(), Budget())
        lineage = it.eval(ci.decl.value, it.globals)
        flows = lineage.get("flows")
        fields = sorted({f.get("field_name") for f in flows})
        assert any(f.endswith(".tier") for f in fields), fields
        assert any(f.endswith(".id") for f in fields), fields
        return (f"derived {len(flows)} flows over {fields}, highest "
                f"classification {lineage.get('highest_classification')!r}")
    case("a pipeline's field lineage is derived from its stages", t_lineage)

    def t_lineage_classification():
        # Reading a personal field must raise the recorded classification.
        src = SRC.replace(
            "  source ids: List<Text> = List.map(rows, fn(c: CustomerV3) => c.id)",
            "  source ids: List<Text> = List.map(rows, fn(c: CustomerV3) => c.email)")
        r, b, _ = build(src)
        assert r is not None and not b.has_errors, b.render(src)
        ci = r.env.consts["customers.tier_report_lineage"]
        it = Interpreter(r, led(), Budget())
        lineage = it.eval(ci.decl.value, it.globals)
        assert lineage.get("highest_classification") == "personal", \
            lineage.get("highest_classification")
        return "reading a personal field raised the pipeline's classification"
    case("lineage records the highest classification that flowed",
         t_lineage_classification)

    print("\ncanonical form")

    def t_renders():
        text = format_module(mod)
        assert "record CustomerV3" in text
        assert "fn migrate_customer_v2_v3(old: CustomerV2) -> CustomerV3" in text
        assert "law invertible_by" in text
        return f"{len(text.splitlines())} lines of canonical Canon"
    case("the lowered module renders as ordinary Canon", t_renders)

    print("\nRESULT:", "pass" if not failures else f"FAIL ({failures})")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())

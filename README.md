# Weft

Schemas, versioned migrations, and data pipelines with derived lineage.

Part of the [Kinode](../kinode-stack) stack. Lowers to [Canon](../canon).

## Install

```sh
pip install -e .
```

## Schemas and a migration

```weft
module customers

schema Applicant v1 {
  intent "An applicant as stored before employment history was collected."
  field id: Text key
  field email: Text classify personal unique
  field legal_name: Text classify personal
  field annual_income: Int
  retention 2555 days
  index email
  invariant annual_income >= 0
}

schema Applicant v2 {
  field id: Text key
  field email: Text classify personal unique
  field legal_name: Text classify personal
  field annual_income: Int
  field months_employed: Int = 0
  retention 2555 days
  index email
  invariant annual_income >= 0
}

migrate Applicant v1 -> v2 {
  intent "Add employment tenure, defaulting existing records to unknown."
  forward months_employed = 0
}
```

## The problem it solves

A schema change is two changes — the new shape, and the path from the old one —
and the second is usually written by hand, later, by someone who no longer
remembers the first. Weft derives what it can and refuses what it cannot.

**Every differing field must be accounted for.** The compiler knows exactly
which fields changed, so the diagnostic names them:

```
error[CANON-E0102]: migration Applicant v1 -> v2 does not supply a value for segment
  fields: ['segment']
  try: forward segment = <expression over old>
```

**An irreversible migration must say so.** Otherwise it is an error, because
whether a migration can be rolled back determines whether a bad deployment can
be:

```
error[CANON-E0102]: migration Applicant v1 -> v2 removes display_name without
                    a way to restore it
  try: backward display_name = <expression over new>
  try: or declare the migration irreversible and say why
       migrate Applicant v1 -> v2 lossy "reason"
```

**Reversible migrations are verified, not asserted.** Both directions are
generated with an `invertible_by` law attached, so the round trip is checked
against generated records:

```
ok  the round trip is checked by the verifier, not asserted:
    invertible_by verified over 40 generated records
```

**Classifications cannot weaken.** A field `personal` in v1 cannot be `public`
in v2, so a rename or reshape cannot launder protected data.

**Field defaults carry into the generated record**, so adding a field with a
default is a non-breaking change rather than an edit to every literal that
constructs one.

## Pipelines

```weft
pipeline income_band_report(rows: List<ApplicantV2>) -> Int
  intent "Count applicants earning above the affordability threshold."
  uses store.write
{
  source ids: List<Text> = List.map(rows, fn(a: ApplicantV2) => a.id)
  transform above = List.filter(rows, fn(a: ApplicantV2) => a.annual_income >= 40000)
  transform total: Int = List.length(above)
  sink store.write("reports/income_band", Int.to_text(total))
  total
}
```

A pipeline lowers to a function plus a **lineage record**: which fields flowed
from which source to which sink, and the highest data classification that
passed through. Derived from the stages, so it cannot drift from the code.

## What it produces

For the schemas above:

- `record ApplicantV1`, `record ApplicantV2` with classifications, invariants
  and defaults preserved
- `fn migrate_applicant_v1_v2(old: ApplicantV1) -> ApplicantV2`
- `fn rollback_applicant_v2_v1(new: ApplicantV2) -> ApplicantV1`
- `const applicant_v1_v2_compatibility: Text` — `"reversible"` or `"lossy"`
- `const income_band_report_lineage: Lineage`

## Tests

```sh
python tests/smoke_weft.py
```

## Licence

Apache-2.0. Copyright Kinode.

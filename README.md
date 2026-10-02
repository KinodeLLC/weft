# Weft

schemas, versioned migrations, and pipelines whose lineage comes off the code.

part of [kinode](https://github.com/KinodeLLC/kinode-stack). lowers to [canon](https://github.com/KinodeLLC/canon).

## install

```sh
pip install -e .
```

## example

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

## migrations

a schema change is two changes, the new shape and the path from the old one to
it, and the second one usually gets written by hand later by somebody who has
forgotten what the first one was for. weft works out what it can and refuses the
rest

every field that differs between two versions has to be accounted for. the
compiler already knows which ones differ so the error names them

```
error[CANON-E0102]: migration Applicant v1 -> v2 does not supply a value for segment
  fields: ['segment']
  try: forward segment = <expression over old>
```

if you drop a field and there is no way to get it back you have to write `lossy`
and say why, otherwise it is an error, because whether a migration can be
reversed is whether a bad deploy can be rolled back

```
error[CANON-E0102]: migration Applicant v1 -> v2 removes display_name without
                    a way to restore it
  try: backward display_name = <expression over new>
  try: or declare the migration irreversible and say why
```

migrations that can be reversed get both directions generated with an
`invertible_by` law attached, so the verifier runs the round trip against
generated records instead of you asserting somewhere that it works

```
ok  the round trip is checked by the verifier, not asserted:
    invertible_by verified over 40 generated records
```

a field classified `personal` in v1 cannot come out `public` in v2, so renaming
or reshaping a field does not launder protected data

field defaults carry into the generated record, so adding a field with a default
does not break every literal that builds one

## pipelines

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

a pipeline gives you a function plus a lineage record, which fields went from
which source to which sink and the highest classification that passed through
it. it comes off the stages so it cannot drift away from the code

## output

| generated | what it is |
| --- | --- |
| `record ApplicantV1`, `record ApplicantV2` | classifications, invariants and defaults kept |
| `fn migrate_applicant_v1_v2` | v1 to v2 |
| `fn rollback_applicant_v2_v1` | v2 back to v1, only if it is reversible |
| `const applicant_v1_v2_compatibility` | `"reversible"` or `"lossy"` |
| `const income_band_report_lineage` | the field level lineage |

## tests

```sh
python tests/smoke_weft.py
```

## licence

Apache-2.0, Kinode.

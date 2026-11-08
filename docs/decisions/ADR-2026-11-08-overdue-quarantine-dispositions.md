# ADR-2026-11-08: dispositions for the overdue publication quarantines

- Status: accepted
- Owners: `catalog/mappings/publication-quarantine.json`, `catalog/source-inventory.json`
- Scope: the nine providers listed overdue in health issue #87

## Problem

Nine quarantine entries passed their `review_by` dates (2026-11-01 to
2026-11-03) without a decision. They differ in kind: some hold data defects,
others hold redistribution questions about papers from private bodies, which
the statutory exemption for examination questions held under law does not
cover.

## Decision

The maintainer decided on 2026-11-08:

| Provider | Disposition | Reason |
| --- | --- | --- |
| `tqc_cert`, `taigi_cert`, `cpc_recruit` | Publish | Identities are reviewed and no explicit prohibition was found |
| `twc_recruit` | Publish | The corrupt official ROC 107 archive is held by the shared corrupt-payload rule; every other paper publishes |
| `moea_recruit` | Keep withheld until 2027-02-01 | Programme and subject identities of all 1,486 papers are unconfirmed |
| `taisugar_recruit` | Keep withheld until 2027-02-01 | All 184 papers await material review, so nothing would publish |
| `jlpt_cert`, `tii_cert`, `tabf_cert` | Remove the provider | Their terms restrict republication (JLPT policy, TII copyright notice) or grant none (TABF) |

Removed providers move to the inventory's `candidates` as
`intentionally_out_of_scope`. Their adapters, state, local mirror files,
mirror backups and Actions caches are deleted. Git history keeps earlier
commits. Chunghwa Post's recruitment papers, served from `svc.tabf.org.tw`,
are a separate provider and stay published.

## Consequences

Reinstating a removed source requires new evidence of permission and the
[add a provider](../contributing/add-a-provider.md) gates. Unlike a quarantine,
a removal shrinks the source-coverage denominator; this record keeps that
reduction explicit.

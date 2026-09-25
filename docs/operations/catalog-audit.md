# Catalog audit

Catalog and history audits are read-only evidence gates unless paired with an explicit migration or publication command.

## Identity and bundle purity

```bash
uv run python -m app audit-catalog --repo-root . --site-id default --strict --output .tmp/catalog-audit.json
```

The strict audit fails when a paper lacks a resolved identity, review records are not isolated in their approved event bundles, or the persisted review queue is stale or incomplete. Its report describes mixed legacy groups and includes the publication backlog; the command also warns when that backlog is nonempty. Those findings are advisory here and are checked by separate publication and history gates. Fix the executable catalog, mapping, or normalizer rather than documenting an exception in prose.

## Event-level retained history

```bash
uv run python -m app history-audit --repo-root . --site-id default --strict --output .tmp/history-audit.json
```

The history audit reconciles raw events, normalized records, mirror payloads, publication, coverage exceptions, and quarantine. With `--probe-sources`, source-only events and failed source discovery count as parser gaps. CI adds `--skip-mirror-check` because the gitignored mirror is absent there; operators with retained mirror state must run the full check.

## Reviewed source scope

```bash
uv run python scripts/validate_source_inventory.py
```

This gate compares `catalog/source-inventory.json` with runtime provider membership and checked-in local state. It reports source-manifest coverage and fails on invalid reviewed scope.

## Migration and publication sequence

When an identity correction affects retained records:

```bash
uv run python -m app migrate-catalog --repo-root . --site-id default
uv run python -m app audit-catalog --repo-root . --site-id default --strict
uv run python -m app publish-site --site-id default --repository <owner>/<repo>
uv run python -m app plan-release --repo-root . --site-id default --output .tmp/release-plan.json
```

Review the migration diff and generated release plan before external release mutation.

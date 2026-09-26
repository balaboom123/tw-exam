# Workflow ownership

Workflow filenames are implementation details; ownership determines what they may change.

| Workflow class | Scope | May write |
| --- | --- | --- |
| provider discovery/sync | one named provider | provider state, provider mirror, scoped publication inputs; may invoke the site publisher for affected bundles |
| audit | repository or selected providers/sites | reports and explicitly reviewed generated corrections |
| site publication/release | one named site | site bundles, feeds, release plans, and assigned assets |
| deploy | one named site | frontend build and deployment output |
| CI | checked-in repository | no persistent runtime state |

Provider workflows may pass a scoped publish plan to the site publisher, which owns site state and release assets. They must not invent release assignments. Site workflows must not parse official sources or mutate provider identity. Deploy workflows consume site feeds rather than raw provider crawl state.

Provider sync workflows restore the latest mirror cache by provider prefix and save a new run-specific cache after a sync attempt, including a failed attempt. This preserves downloaded files when a run fails after fetching them. CEEC AST calls `_sync-provider.yml`, which publishes affected bundles and commits provider and site state together only after release upload succeeds. Actions cache remains subject to eviction, so this is an interim recovery measure rather than durable mirror storage. Later provider callers must sequence writes to the shared site metadata.

## Generated-state commit guard

`.github/scripts/commit-and-push.sh` runs `scripts/check_sync_floor.py` and
`scripts/validate_publication.py` before committing generated data. The floor
guard resolves provider IDs from staged `data/providers/<provider_id>/` paths
and refuses event or paper counts below the reviewed `local_state` floor in
`catalog/source-inventory.json`. The publication preflight rejects unresolved
sync failures, discovery-manifest drift, and provider/site eligibility drift.
This is required because pushes made with `GITHUB_TOKEN` do not start the
normal push CI workflow. Source growth is allowed only while the resulting
tree remains deployable. A genuine reviewed removal requires updating the
inventory floor; a transiently incomplete sync must be rerun.

A failed sync may write partial state inside its runner so the shared guard can
diagnose it, but that state is not committed to `main`. A provider workflow
that publishes site bundles commits provider and site state together only after
its release upload succeeds. The failed Actions run and, for scheduled
workflows, its single workflow-health issue retain the operational evidence
while the last deployable provider state stays checked in. Pages ignores failed upstream
workflow runs; a successful sync or the daily Pages backstop rebuilds and
deploys the current site feed. CI and the generated-state commit guard own
the Python and catalog gates.

## Health reporting

`workflow-health` runs once daily. It inspects each scheduled workflow's
latest run and keeps one labelled issue for a failure, timeout, or cancellation
that lasted at least the workflow's timeout. Short cancellations from
superseded Pages deployments are ignored. A later success closes a failure
issue; repeated failures do not add notification comments.

A successful run also raises an issue when its duration exceeds three times
the median of at least three prior successful runs (up to ten are checked).
The issue points to the latest slow run; the next normal-duration success
closes it.

The same pass uses each workflow's schedule to set a staleness window and
accepts a successful manual rerun as recovery. This also detects schedules
that stop firing entirely.

Manual diagnosis uses:

```bash
uv run python scripts/check_sync_floor.py --repo-root . -- data/providers/<provider_id>/papers/<year>.json
```

## Change checklist

When adding or changing a workflow:

1. Name its provider or site owner explicitly.
2. Use scoped provider and site paths.
3. Keep permissions least-privileged and secrets limited to the step that needs them.
4. Preserve strict catalog, source-inventory, publication, history, release-plan, and documentation gates.
5. Add or update `tests/test_workflows.py` for structural workflow contracts.
6. Update the [runbook](runbook.md) and [recovery guide](recovery.md) when operator behavior changes.

Use [CI/CD and release](ci-cd-and-release.md) for gate and release topology details.

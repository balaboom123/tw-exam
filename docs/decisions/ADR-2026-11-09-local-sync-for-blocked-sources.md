# ADR-2026-11-09: sync sources that block hosted runners from the maintainer's machine

- Status: implemented
- Owners: `hosted_runner_reachable` in each adapter's `provider.py`; the
  certification, education and employment sync matrices
- Scope: `moea_recruit`, `taipower_recruit`, `teacher_recruit_kaohsiung`,
  `sfi_cert`, `wdasec_skill`

## Problem

The plan treats a failing fetch as "Source down": the leg fails, the next
schedule retries, and a health issue names the leg. For five sources the
failure never clears. On every GitHub-hosted run they stop at discovery: MOEA
and Taipower answer HTTP 403, Kaohsiung reports no route ("Network is
unreachable"), and SFI and WDASEC time out (runs 42556532895, 42556535298 and
42556538017 on 2026-11-09). None has passed on a hosted runner since at least
2026-09-29, except SFI on 2026-09-30. From the maintainer's machine in Taiwan,
the same adapters discover within a second. The sources block hosted runners
rather than being down, so the sync workflows stay red, and health issues #70,
#71 and #77 can never close or show a new failure clearly.

## Decision

Each of the five adapters sets `hosted_runner_reachable = False`. They leave
the scheduled matrices, and no workflow owns them: a manual hosted workflow, as
for Hakka and TOCFL, would hit the same block. The maintainer refreshes and
publishes them locally with the runbook's
[provider refresh](../operations/runbook.md#refresh-one-provider) and
[publication](../operations/runbook.md#publish-the-default-site) steps.

A self-hosted runner in Taiwan was rejected: this repository is public, and
GitHub advises against self-hosted runners for public repositories because a
pull request can run code on them.

## Consequences

Their updates depend on the maintainer's local runs; until now they did anyway,
because no hosted run succeeded. Tainan's timeout (it is also unreachable from
Taiwan) and GEPT's two dead upstream links remain real outages and stay on the
schedule. When a source serves hosted runners again, restore its matrix row and
remove the flag; the workflow test requires both changes together.

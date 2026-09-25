# Frontend display classification

Status: current presentation reference
Owner: site publication and frontend maintainers

Official identity and display class/subclass labels are resolved by the backend and persisted in the site feed. The frontend uses those published labels to build category filters. It does not classify bundles by name or provider prefix.

## Responsibilities

Backend/catalog owns:

- exam domain, family, series, level, track, variants, and stage;
- bundle membership and purity;
- historical mappings, confidence, review, and migration;
- stable v2 bundle_id.

Frontend owns display order and filter presentation. The published feed must contain a nonempty class and subclass for every bundle; the frontend rejects an incomplete feed instead of guessing labels.

A v2 bundle must expose structured facets such as seriesId, levelId, and trackId. New UI code should render those fields and use the display classifier only to choose presentation grouping. Do not add a frontend regex to repair a backend bundle.

The class display order is in `frontend/src/lib/exam-categories.ts`. Subclasses come from the feed and are sorted for display. A new class absent from the preferred order still appears after known classes.

## Adding or changing UI categories

1. Change the backend mapping or site publication owner for the class or subclass label and validate the generated feed.
2. Adjust frontend display order if a new class needs a preferred position.
3. Run the feed contract test and catalog audit. A UI-only order change does not change bundle grouping or release assets.

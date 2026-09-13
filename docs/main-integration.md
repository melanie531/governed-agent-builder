# Main integration · 2026-09-14

`main` consolidates the 34 remote branch heads fetched for this integration.
The repository previously used `feat/local-first` as its default branch and had
no `main`. The source branches remain intact.

## Resolved behavior

- Create Agent and Platform Admin retain their current Runtime, Gateway,
  optional evaluation, confirmed cleanup, chat, approval, and reporting paths.
- Models combine provider discovery and the published Journey catalog. The
  discovery date filter does not hide published models. Discovery metadata
  alone cannot authorize a model for the builder.
- Model display keeps provider grouping, capability filters, exact identity
  handling, and separate backend counts for granted, callable, and requestable
  models.
- Existing capability requests and their status stay in AI Catalog. New-tool
  requests retain the dedicated tool-request page and administrator handling.
  Legacy general-request backend records remain supported.
- Successful access requests retain their confirmation inside the modal while
  refreshing the catalog. Duplicate requests link to the user's request status.
- Runtime protocol registration retains explicit Opus request/response contracts
  and the existing Haiku transport. The later diagnostic admission, persistent
  budget, and final dispatch deadline checks supersede the earlier standalone
  send prototype; this merge does not activate diagnostic cloud resources.

The merged browser tests use explicit synthetic discovery or published-model
fixtures. They no longer require the superseded recency controls or the old
standalone capability-request page. The new integration regression verifies
that catalog discovery cannot remove published models or grant execution.

## Validation

Validation uses the complete Python suite, the frontend production build, and
both browser suites against isolated local databases. Cloud dependencies in
these tests are simulated; merging source is not a new hosted AWS acceptance
run or deployment.

Local results: 1,617 Python tests passed with 4 skipped; all 51 catalog and
console browser tests and all 9 Create Agent / Platform Admin browser tests
passed. The frontend production build also passed.

CI fetches full repository history because the IAM regression compares its
template against a reviewed ancestor.

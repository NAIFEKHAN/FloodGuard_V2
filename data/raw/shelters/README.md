# Shelter ingestion

No authoritative Nilgiris shelter records are currently available in this
project. `shelters.csv` is an empty ingestion interface, not a shelter
dataset. Do not add DEMO coordinates, inferred capacities, or contact details.

Each row must cite its source. Set `verification_status` to `verified` only
when the facility, its location, and its designation have supporting source
evidence and `verified_at` is recorded. Use `unverified` or `needs_review`
otherwise. Coordinates outside geographic bounds or the supplied Nilgiris
study extent are rejected/flagged; do not silently move them.

Set `operational_status` to `operational` only when a source confirms current
operation and the source/update date is recorded. Use `not_operational` for a
confirmed closure and `unknown` when operation has not been verified. Only
facilities with both `verification_status=verified` and
`operational_status=operational` are eligible for route requests; the two
statuses are intentionally independent.

The API normalizes records to the documented shelter response, reports
verification and provenance, and excludes non-verified facilities from route
recommendations.

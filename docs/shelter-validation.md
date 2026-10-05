# Retired shelter placeholders

The following ten entries were legacy frontend-only placeholders. They were
removed from operational maps, shelter selection, and route planning. Their
names are listed only to make the retired demonstration content auditable;
they are **not verified facilities**, and must not be used for emergency
decisions or re-imported as shelter records.

| Retired ID | Legacy display name |
|---|---|
| `DEMO-SHELTER-01` | Government Arts College Relief Center (DEMO) |
| `DEMO-SHELTER-02` | St. Joseph Multipurpose Hall Relief Center (DEMO) |
| `DEMO-SHELTER-03` | Coonoor Municipal Community Hall (DEMO) |
| `DEMO-SHELTER-04` | Providence Secondary School Refuge (DEMO) |
| `DEMO-SHELTER-05` | Kotagiri Town Panchayat Community Shelter (DEMO) |
| `DEMO-SHELTER-06` | Kodanad View Emergency Relief Post (DEMO) |
| `DEMO-SHELTER-07` | Gudalur Government Higher Secondary School (DEMO) |
| `DEMO-SHELTER-08` | Pandalur Taluk Disaster Relief Hall (DEMO) |
| `DEMO-SHELTER-09` | Kundah Hydro Project Community Center (DEMO) |
| `DEMO-SHELTER-10` | Ketti Valley Community Welfare Center (DEMO) |

## Coordinate sanity check

The retired source contained ten numeric coordinate pairs. A one-time check
against WGS84 numeric bounds and the project study extent found all ten pairs
within both checks; none fell outside the extent. This only establishes that
the numbers are geographically bounded and fall within the broad supplied
extent. It does **not** establish that any named facility exists at those
locations, is designated as a shelter, or is operational. No coordinate values
are reproduced here because they are unverified synthetic placeholders.

The current `data/raw/shelters/shelters.csv` is a header-only ingestion
template. Route eligibility requires independently sourced facility details,
verified coordinates, and a verification date; passing extent checks alone is
not verification.

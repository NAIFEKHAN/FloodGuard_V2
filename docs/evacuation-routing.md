# Shelter inventory and road routing

## Shelter data

FloodGuard does not ship any assumed or demonstration shelter locations. The
versioned, header-only ingestion template is
[`data/raw/shelters/shelters.csv`](../data/raw/shelters/shelters.csv); its
instructions are in [`data/raw/shelters/README.md`](../data/raw/shelters/README.md).
Only records with a source, verification date, valid WGS84 coordinates, and
coordinates inside the supplied village-boundary extent can be used for route
requests. Invalid or incomplete verification claims are downgraded to
`needs_review`.

`GET /api/shelters` reports the inventory, verification coverage, and number of
route-eligible facilities. `GET /api/villages/{village_code}/shelters` returns
eligible facilities ordered by straight-line distance as a prefilter only;
that ordering is not a road-distance or suitability recommendation.

## Road-route requests

`GET /api/evacuation-route` accepts a verified `shelter_id` and either a
`village_code` or an explicitly provided `origin_lat` / `origin_lon` pair. A
village origin is derived from its LGD-matched boundary; an explicitly supplied
coordinate origin must lie within the validated study extent. Origins must be
snapped to the road network by the provider. The API returns provider road geometry,
distance, estimated duration, warning/historical-context intersections, and
available route alternatives. FloodGuard does not draw a straight-line
fallback when road routing is unavailable.

Routing provider configuration is server-side:

- `ROUTING_PROVIDER=osrm` (default) uses the OSRM routing API. The public
  demonstration endpoint is not intended for production-scale traffic.
- `ROUTING_PROVIDER=openrouteservice` requires the server-only
  `ROUTING_API_KEY`.
- `ROUTING_BASE_URL` can point to an approved self-hosted or provisioned
  provider endpoint.

The browser calls only FloodGuard's backend route endpoint; provider credentials
must never be placed in frontend assets. Provider errors are surfaced as an
unavailable route, not as an alternate invented path.

## Limits

Route exposure checks intersect returned road geometry with current FloodGuard
warning-stage villages, very-high baseline-susceptibility villages, and
historical-event coordinates. Those intersections do not prove that a road is
closed, hazardous, or safe. The app has no verified shelter inventory at
present, so route requests are disabled until source records are supplied.
Always verify facility availability, road access, and emergency instructions
with local authorities.

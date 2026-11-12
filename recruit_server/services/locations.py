"""Recruiting geography and physical work-location Oracle queries."""

LOCATION_SPECS = {
    "work_location": ("locations", "LocationId", "LocationId,LocationName,LocationCode"),
    "geography": ("recruitingLocationsLOV", "GeographyId", "GeographyId,LocationFQN"),
}


class Locations:
    def __init__(self, client):
        self.client = client

    def fetch(self, kind, ids):
        resource, id_field, projection = LOCATION_SPECS[kind]
        params = {"fields": projection}
        if kind == "geography":
            if len(ids) != 1:
                raise ValueError("Geography lookups require one verified ID")
            # GET /recruitingLocationsLOV?finder=findByGeographyId;GeographyId=...:
            # resolve requisition PrimaryLocationId to the recruiting geography LocationFQN.
            params["finder"] = "findByGeographyId;GeographyId=" + ids[0]
        else:
            # GET /locations?q=LocationId=... (or in (...)): resolve PrimaryWorkLocationId to name/code.
            params["q"] = id_field + ("=" + ids[0] if len(ids) == 1 else " in (" + ",".join(ids) + ")")
        return self.client.collection(resource, params)

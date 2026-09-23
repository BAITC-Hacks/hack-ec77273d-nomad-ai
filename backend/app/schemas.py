"""Response models for the local Nomad AI API."""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    as_of_date: str
    counts: dict[str, int]


class ImportResponse(BaseModel):
    import_id: str
    as_of_date: str
    counts: dict[str, int]


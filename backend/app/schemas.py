from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    service: str = "echoward-backend"
    environment: str
    database_connected: bool

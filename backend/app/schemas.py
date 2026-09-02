from typing import Literal

from pydantic import BaseModel, Field

# Agora channel names: 1-64 bytes, letters/digits/space and a fixed punctuation set.
CHANNEL_NAME_PATTERN = r"^[a-zA-Z0-9 !#$%&()+\-:;<=.>?@\[\]^_{}|~,]{1,64}$"


class HealthResponse(BaseModel):
    status: str
    service: str = "echoward-backend"
    environment: str
    database_connected: bool


class RtcTokenRequest(BaseModel):
    channel: str = Field(pattern=CHANNEL_NAME_PATTERN)
    uid: int = Field(gt=0, lt=2**32 - 1)
    role: Literal["publisher", "subscriber"] = "publisher"


class RtcTokenResponse(BaseModel):
    app_id: str
    channel: str
    uid: int
    token: str
    expires_in: int


class StartAgentRequest(BaseModel):
    channel: str = Field(pattern=CHANNEL_NAME_PATTERN)
    # M6.2: optional link so app/voice.py's proactive interventions know which
    # live Agora agent to speak through for this incident's coordination
    # findings - purely additive, agent/start still works without it.
    incident_id: str | None = None


class StartAgentResponse(BaseModel):
    agent_id: str
    channel: str
    agent_uid: int
    status: str


class StopAgentRequest(BaseModel):
    agent_id: str = Field(min_length=1)


class StopAgentResponse(BaseModel):
    agent_id: str
    status: str

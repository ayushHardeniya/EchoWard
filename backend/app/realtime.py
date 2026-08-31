"""In-process WebSocket fanout for live incident-state updates.

One FastAPI process + SQLite is enough for this MVP — connections are just a
`dict[incident_id, set[WebSocket]]` in memory. No Redis/pub-sub: if this process
restarts, clients reconnect and refetch state via the REST API, same as any
other transient disconnect.
"""

import logging
from collections import defaultdict

from fastapi import WebSocket

from app.incident_models import IncidentState

logger = logging.getLogger("echoward.realtime")


class IncidentConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, incident_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[incident_id].add(websocket)

    def disconnect(self, incident_id: str, websocket: WebSocket) -> None:
        self._connections[incident_id].discard(websocket)
        if not self._connections[incident_id]:
            self._connections.pop(incident_id, None)

    def connection_count(self, incident_id: str) -> int:
        return len(self._connections.get(incident_id, ()))

    async def broadcast_state(self, incident_id: str, state: IncidentState) -> None:
        sockets = list(self._connections.get(incident_id, ()))
        if not sockets:
            return
        message = {
            "type": "incident.updated",
            "incident_id": incident_id,
            "state": state.model_dump(mode="json"),
        }
        for ws in sockets:
            try:
                await ws.send_json(message)
            except Exception as exc:
                logger.warning("Dropping unresponsive incident stream connection: %s", exc)
                self.disconnect(incident_id, ws)


manager = IncidentConnectionManager()

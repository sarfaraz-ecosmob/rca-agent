"""
WebSocket manager for real-time communication with the dashboard.

Broadcasts alerts, analysis results, container updates, and statistics
to all connected WebSocket clients.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from fastapi import WebSocket, WebSocketDisconnect

from src.models.schemas import WSMessage

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Manages WebSocket connections and broadcasts.
    
    Features:
    - Tracks connected clients
    - Broadcasts to all or specific clients
    - Handles disconnection gracefully
    - Supports message types for filtering
    """

    def __init__(self):
        self._connections: Set[WebSocket] = set()
        self._connection_meta: Dict[WebSocket, Dict[str, Any]] = {}

    async def connect(self, websocket: WebSocket, client_info: Optional[Dict] = None) -> None:
        """Accept and register a new WebSocket connection."""
        await websocket.accept()
        self._connections.add(websocket)
        self._connection_meta[websocket] = client_info or {}
        logger.info(
            f"WebSocket client connected. Total connections: {len(self._connections)}"
        )

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a disconnected WebSocket client."""
        self._connections.discard(websocket)
        self._connection_meta.pop(websocket, None)
        logger.info(
            f"WebSocket client disconnected. Total connections: {len(self._connections)}"
        )

    async def broadcast(self, message: WSMessage) -> None:
        """Broadcast a message to all connected clients."""
        if not self._connections:
            return

        payload = message.model_dump_json()
        disconnected = set()

        for websocket in self._connections:
            try:
                await websocket.send_text(payload)
            except WebSocketDisconnect:
                disconnected.add(websocket)
            except Exception as e:
                logger.warning(f"WebSocket send error: {e}")
                disconnected.add(websocket)

        # Clean up disconnected clients
        for ws in disconnected:
            self.disconnect(ws)

    async def broadcast_type(self, message_type: str, data: Dict[str, Any]) -> None:
        """Broadcast a specific message type to all clients."""
        message = WSMessage(
            type=message_type,
            data=data,
            timestamp=datetime.utcnow(),
        )
        await self.broadcast(message)

    async def send_to(self, websocket: WebSocket, message: WSMessage) -> None:
        """Send a message to a specific client."""
        try:
            await websocket.send_text(message.model_dump_json())
        except WebSocketDisconnect:
            self.disconnect(websocket)
        except Exception as e:
            logger.warning(f"WebSocket send error: {e}")

    @property
    def active_connections(self) -> int:
        """Get count of active connections."""
        return len(self._connections)

    def get_connection_info(self) -> List[Dict[str, Any]]:
        """Get information about all connections."""
        return [
            {"meta": meta}
            for ws, meta in self._connection_meta.items()  # noqa: F841
        ]


# Global WebSocket manager instance
ws_manager = ConnectionManager()


async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for real-time dashboard updates."""
    await ws_manager.connect(websocket)

    try:
        while True:
            # Wait for incoming messages (keep-alive, subscriptions)
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
                msg_type = message.get("type", "")

                if msg_type == "ping":
                    await ws_manager.send_to(
                        websocket,
                        WSMessage(type="pong", data={"timestamp": datetime.utcnow().isoformat()}),
                    )
                elif msg_type == "subscribe":
                    logger.info(f"Client subscribed to updates: {message.get('channels', [])}")

            except json.JSONDecodeError:
                pass

    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as e:
        logger.warning(f"WebSocket error: {e}")
        ws_manager.disconnect(websocket)


# --- Event broadcasting functions ---


async def broadcast_alert(alert_data: Dict[str, Any]) -> None:
    """Broadcast a new alert to all dashboard clients."""
    await ws_manager.broadcast_type("alert", alert_data)


async def broadcast_analysis(analysis_data: Dict[str, Any]) -> None:
    """Broadcast a new RCA analysis result to all dashboard clients."""
    await ws_manager.broadcast_type("analysis", analysis_data)


async def broadcast_container_update(container_data: Dict[str, Any]) -> None:
    """Broadcast container status change to all dashboard clients."""
    await ws_manager.broadcast_type("container_update", container_data)


async def broadcast_stats(stats_data: Dict[str, Any]) -> None:
    """Broadcast updated statistics to all dashboard clients."""
    await ws_manager.broadcast_type("stats", stats_data)


async def broadcast_notification(notification_data: Dict[str, Any]) -> None:
    """Broadcast notification status to all dashboard clients."""
    await ws_manager.broadcast_type("notification", notification_data)

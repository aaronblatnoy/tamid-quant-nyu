"""AISStream.io WebSocket client with exponential-backoff reconnect."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from typing import TYPE_CHECKING

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

AISSTREAM_URL = "wss://stream.aisstream.io/v0/stream"

DEFAULT_MESSAGE_TYPES = (
    "PositionReport",
    "StandardClassBPositionReport",
    "ExtendedClassBPositionReport",
    "ShipStaticData",
    "StaticDataReport",
)
GLOBAL_BBOX = (((-90.0, -180.0), (90.0, 180.0)),)

INITIAL_BACKOFF_S = 1.0
MAX_BACKOFF_S = 300.0


def _build_subscription(
    api_key: str,
    bounding_boxes: tuple[tuple[tuple[float, float], tuple[float, float]], ...],
    message_types: tuple[str, ...],
) -> str:
    return json.dumps(
        {
            "APIKey": api_key,
            "BoundingBoxes": [[list(p) for p in bbox] for bbox in bounding_boxes],
            "FilterMessageTypes": list(message_types),
        }
    )


async def stream(
    api_key: str,
    *,
    bounding_boxes: tuple[tuple[tuple[float, float], tuple[float, float]], ...] = GLOBAL_BBOX,
    message_types: tuple[str, ...] = DEFAULT_MESSAGE_TYPES,
) -> AsyncIterator[bytes]:
    """Yield raw JSON message bytes from AISStream forever.

    Reconnects automatically on disconnect with exponential backoff
    (1s → 2s → ... capped at 5min). Caller can stop by cancelling.
    """
    if not api_key:
        raise ValueError("AISSTREAM_API_KEY is empty")

    sub = _build_subscription(api_key, bounding_boxes, message_types)
    backoff = INITIAL_BACKOFF_S
    rng = secrets.SystemRandom()
    while True:
        try:
            async with websockets.connect(AISSTREAM_URL) as ws:
                await ws.send(sub)
                logger.info("connected to aisstream")
                backoff = INITIAL_BACKOFF_S
                async for raw in ws:
                    yield raw if isinstance(raw, bytes) else raw.encode("utf-8")
        except (ConnectionClosed, OSError, InvalidStatus, TimeoutError) as e:
            jitter = rng.uniform(0, backoff * 0.25)
            wait = min(backoff + jitter, MAX_BACKOFF_S)
            logger.warning(
                "aisstream disconnected (%s), reconnecting in %.1fs",
                type(e).__name__,
                wait,
            )
            await asyncio.sleep(wait)
            backoff = min(backoff * 2, MAX_BACKOFF_S)

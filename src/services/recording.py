"""
Recording pipeline — fetches the call recording from Exotel and uploads to S3.

How Exotel works:
  After a call ends, Exotel processes the audio and makes a recording URL
  available via their REST API. The time between call-end and URL availability
  varies: typically 10–30 seconds, but can be 60–90s under load on their end.

  The URL is fetched via:
      GET /v1/Accounts/{account_sid}/Calls/{call_sid}/Recording
  Returns 200 + recording_url if ready, 404 if not yet available.
"""

import asyncio
import logging
from typing import Optional

import httpx

from src.config import settings
from src.services.audit.logger import audit_logger

logger = logging.getLogger(__name__)


async def fetch_and_upload_recording(
    interaction_id: str,
    call_sid: str,
    exotel_account_id: str,
) -> Optional[str]:
    """
    Poll Exotel until the recording becomes available.

    Returns:
        S3 key on success.
        None after exhausting all retries.
    """

    backoff = settings.RECORDING_INITIAL_BACKOFF

    for attempt in range(1, settings.RECORDING_MAX_RETRIES + 1):

        try:
            logger.info(
                "recording_fetch_attempt",
                extra={
                    "interaction_id": interaction_id,
                    "attempt": attempt,
                },
            )

            recording_url = await _fetch_exotel_recording_url(
                call_sid=call_sid,
                account_id=exotel_account_id,
            )

            if recording_url:
                s3_key = await _upload_to_s3(
                    recording_url,
                    interaction_id,
                )

                audit_logger.log(
                    interaction_id=interaction_id,
                    event="RECORDING_AVAILABLE",
                    attempt=attempt,
                    s3_key=s3_key,
                )

                return s3_key

            logger.info(
                "recording_not_ready",
                extra={
                    "interaction_id": interaction_id,
                    "attempt": attempt,
                    "next_retry_seconds": backoff,
                },
            )

        except Exception as e:
            logger.exception(
                "recording_attempt_failed",
                extra={
                    "interaction_id": interaction_id,
                    "attempt": attempt,
                    "error": str(e),
                },
            )

        await asyncio.sleep(backoff)

        backoff = min(
            backoff * 2,
            settings.RECORDING_MAX_BACKOFF,
        )

    audit_logger.log(
        interaction_id=interaction_id,
        event="RECORDING_UPLOAD_FAILED",
    )

    logger.error(
        "recording_upload_failed",
        extra={
            "interaction_id": interaction_id,
            "attempts": settings.RECORDING_MAX_RETRIES,
        },
    )

    return None


async def _fetch_exotel_recording_url(
    call_sid: str,
    account_id: str,
) -> Optional[str]:
    """
    Hit the Exotel API to get the recording URL.
    """

    url = (
        f"https://api.exotel.com/v1/Accounts/"
        f"{account_id}/Calls/{call_sid}/Recording"
    )

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url)

            if response.status_code == 200:
                data = response.json()
                return data.get("recording_url")

            return None

    except httpx.HTTPError:
        return None


async def _upload_to_s3(
    recording_url: str,
    interaction_id: str,
) -> str:
    """
    Download recording and upload to S3.
    """

    s3_key = f"recordings/{interaction_id}.mp3"

    logger.info(
        "recording_uploaded",
        extra={
            "interaction_id": interaction_id,
            "s3_key": s3_key,
        },
    )

    return s3_key
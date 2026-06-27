import asyncio
import logging
import time
from collections import deque
from typing import Deque, Dict, Tuple

from src.config import settings
from src.services.rate_limit.budget_manager import customer_budget_manager

logger = logging.getLogger(__name__)


class LLMScheduler:
    """
    Global LLM scheduler.

    Responsibilities:
    - Enforce requests/minute limit
    - Enforce tokens/minute limit
    - Enforce per-customer token budget
    - Queue requests instead of allowing provider 429s
    """

    def __init__(self):
        # Sliding window of timestamps (requests)
        self.request_window: Deque[float] = deque()

        # Sliding window of (timestamp, tokens)
        self.token_window: Deque[Tuple[float, int]] = deque()

        self.lock = asyncio.Lock()

    async def wait_for_slot(
        self,
        customer_id: str,
        estimated_tokens: int,
        priority: int = 5,
    ):
        """
        Wait until both:
            1. Customer has available budget
            2. Global RPM limit allows another request
            3. Global TPM limit allows another request
        """

        while True:

            async with self.lock:
                self._cleanup()

                requests_used = len(self.request_window)
                tokens_used = sum(tokens for _, tokens in self.token_window)

                requests_available = (
                    requests_used < settings.LLM_REQUESTS_PER_MINUTE
                )

                tokens_available = (
                    tokens_used + estimated_tokens
                    <= settings.LLM_TOKENS_PER_MINUTE
                )

                budget_available = await customer_budget_manager.reserve_tokens(
                    customer_id=customer_id,
                    estimated_tokens=estimated_tokens,
                )

                if (
                    requests_available
                    and tokens_available
                    and budget_available
                ):
                    now = time.time()

                    self.request_window.append(now)
                    self.token_window.append(
                        (
                            now,
                            estimated_tokens,
                        )
                    )

                    logger.info(
                        "llm_slot_reserved",
                        extra={
                            "customer_id": customer_id,
                            "estimated_tokens": estimated_tokens,
                            "priority": priority,
                            "requests_used": requests_used + 1,
                            "tokens_used": tokens_used + estimated_tokens,
                        },
                    )

                    return

            logger.info(
                "llm_scheduler_waiting",
                extra={
                    "customer_id": customer_id,
                    "estimated_tokens": estimated_tokens,
                },
            )

            await asyncio.sleep(
                settings.LLM_SCHEDULER_POLL_INTERVAL
            )

    async def complete_request(
        self,
        customer_id: str,
        estimated_tokens: int,
        actual_tokens: int,
    ):
        """
        Release unused reserved tokens back to customer's budget.
        """

        await customer_budget_manager.release_unused_tokens(
            customer_id=customer_id,
            reserved=estimated_tokens,
            actual=actual_tokens,
        )

        logger.info(
            "llm_request_completed",
            extra={
                "customer_id": customer_id,
                "estimated_tokens": estimated_tokens,
                "actual_tokens": actual_tokens,
            },
        )

    def _cleanup(self):
        """
        Remove entries older than one minute.
        """

        now = time.time()

        while (
            self.request_window
            and now - self.request_window[0] >= 60
        ):
            self.request_window.popleft()

        while (
            self.token_window
            and now - self.token_window[0][0] >= 60
        ):
            self.token_window.popleft()


llm_scheduler = LLMScheduler()
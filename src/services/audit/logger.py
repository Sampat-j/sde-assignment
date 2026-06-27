import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("voicebot.audit")


class AuditLogger:
    """
    Centralized structured audit logger.

    Every important workflow transition should go through here so
    an interaction can be reconstructed from logs.
    """

    def log(
        self,
        *,
        interaction_id: str,
        event: str,
        customer_id: Optional[str] = None,
        campaign_id: Optional[str] = None,
        **metadata: Dict[str, Any],
    ):
        logger.info(
            event,
            extra={
                "interaction_id": interaction_id,
                "event": event,
                "customer_id": customer_id,
                "campaign_id": campaign_id,
                **metadata,
            },
        )


audit_logger = AuditLogger()
"""Bot replies to volunteers through Evolution API v2 `sendText`.

POST {BVP_EVOLUTION_URL}/message/sendText/{instance}
     apikey: <BVP_EVOLUTION_API_KEY>
     {"number": "5511999999999", "text": "..."}

A reply that fails is logged and dropped: it must never break intake.
"""

import logging

import httpx

logger = logging.getLogger(__name__)


def phone_number_from_jid(jid: str) -> str:
    """ "5511999999999@s.whatsapp.net" → "5511999999999" (what sendText expects)."""
    return jid.split("@", 1)[0]


class EvolutionClient:
    def __init__(
        self, http_client: httpx.Client, base_url: str, api_key: str, instance: str
    ) -> None:
        self._http = http_client
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._instance = instance

    def send_text(self, recipient_jid: str, text: str) -> bool:
        """Send one WhatsApp text. True if Evolution accepted it."""
        try:
            response = self._http.post(
                f"{self._base_url}/message/sendText/{self._instance}",
                json={"number": phone_number_from_jid(recipient_jid), "text": text},
                headers={"apikey": self._api_key},
            )
        except httpx.HTTPError as error:
            logger.warning("bot reply to %s failed: %s", recipient_jid, error)
            return False
        if response.is_error:
            logger.warning(
                "bot reply to %s failed: HTTP %s %s",
                recipient_jid,
                response.status_code,
                response.text[:200],
            )
            return False
        return True

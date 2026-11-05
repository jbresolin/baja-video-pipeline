"""Bot replies to volunteers through Evolution API v2 `sendText`, and the instance status.

POST {BVP_EVOLUTION_URL}/message/sendText/{instance}
     apikey: <BVP_EVOLUTION_API_KEY>
     {"number": "5511999999999", "text": "..."}

A reply that fails is logged and dropped: it must never break intake.
The admin Settings page also asks for the instance's connection state and
webhook (instance_status).
"""

import logging

import httpx

from shared.models import EvolutionStatus

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

    def instance_status(self) -> EvolutionStatus:
        """Connection state and webhook of the Baja Bot instance, for the Settings page.

        GET /instance/connectionState/{instance} → {"instance": {"state": "open"}}
        GET /webhook/find/{instance}             → {"enabled": true, "url": ...} (or null)
        Never raises: what could not be read is None, with the reason in `error`.
        """
        status = EvolutionStatus(instance=self._instance)
        problems = []
        try:
            state = self._get_json(f"/instance/connectionState/{self._instance}")
            status.state = state["instance"]["state"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            problems.append(f"connection state: {error}")
        try:
            webhook = self._get_json(f"/webhook/find/{self._instance}")
            if isinstance(webhook, dict) and isinstance(webhook.get("webhook"), dict):
                webhook = webhook["webhook"]  # some versions wrap it
            if isinstance(webhook, dict):
                status.webhook_url = webhook.get("url")
                status.webhook_enabled = bool(webhook.get("enabled"))
            else:
                status.webhook_enabled = False
        except (httpx.HTTPError, ValueError) as error:
            problems.append(f"webhook: {error}")
        status.error = "; ".join(problems) or None
        return status

    def _get_json(self, path: str):
        response = self._http.get(f"{self._base_url}{path}", headers={"apikey": self._api_key})
        response.raise_for_status()
        return response.json() if response.content.strip() else None

"""Bot replies to volunteers through Evolution API v2.3.7: texts and reactions.

    POST {BVP_EVOLUTION_URL}/message/sendText/{instance}       apikey: <key>
         {"number": "5511999999999", "text": "...",
          "quoted": {"key": {"id", "remoteJid", "fromMe"}, "message": {...}}}
    POST {BVP_EVOLUTION_URL}/message/sendReaction/{instance}   apikey: <key>
         {"key": {"remoteJid": "...@lid", "fromMe": false, "id": "..."}, "reaction": "✅"}

Request bodies as Evolution 2.3.7 validates and uses them (tag 2.3.7 of
github.com/EvolutionAPI/evolution-api):
* src/validate/message.schema.ts: `textMessageSchema` (number + text
  required, optional `quoted` = `quotedOptionsSchema`: key.id required,
  plus `message`) and `reactionMessageSchema` (key.id, key.remoteJid and
  key.fromMe all required, plus `reaction`).
* src/api/dto/sendMessage.dto.ts: `SendTextDto`, `Quoted {key, message}`,
  `SendReactionDto {key, reaction}`.
* src/api/integrations/channel/whatsapp/whatsapp.baileys.service.ts:
  `reactionMessage()` sends to `key.remoteJid` as given (an `@lid` jid is
  accepted as-is by `whatsappNumber()`), and `sendMessageWithTyping()`
  uses `quoted` directly when it carries `message`, else looks the key up
  in Evolution's own message table. We always send `message`, so quoting
  works even when Evolution does not store messages.

A reply that fails is logged and dropped: it must never break intake.
The admin Settings page also asks for the instance's connection state and
webhook (instance_status).
"""

import logging
from typing import Any

import httpx

from shared.models import EvolutionStatus

logger = logging.getLogger(__name__)


def phone_number_from_jid(jid: str) -> str:
    """ "5511999999999@s.whatsapp.net" → "5511999999999" (what sendText expects)."""
    return jid.split("@", 1)[0]


def message_key(remote_jid: str, message_id: str) -> dict[str, Any]:
    """The key of a message someone sent us, exactly as the webhook carried it."""
    return {"remoteJid": remote_jid, "fromMe": False, "id": message_id}


class EvolutionClient:
    def __init__(
        self, http_client: httpx.Client, base_url: str, api_key: str, instance: str
    ) -> None:
        self._http = http_client
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._instance = instance

    def send_text(
        self, recipient_jid: str, text: str, *, quoted: dict[str, Any] | None = None
    ) -> bool:
        """Send one WhatsApp text, optionally as a reply to `quoted` ({key, message}).

        True if Evolution accepted it.
        """
        body: dict[str, Any] = {"number": phone_number_from_jid(recipient_jid), "text": text}
        if quoted is not None:
            body["quoted"] = quoted
        return self._post("sendText", body, f"bot reply to {recipient_jid}")

    def send_text_reporting_failure(self, phone: str, text: str) -> str | None:
        """Send one WhatsApp text to `phone` (digits); None if accepted, else why not.

        For the welcome message (LIVESTACK-44), whose failure the admin sees.
        """
        body = {"number": phone, "text": text}
        return self._post_reporting_failure("sendText", body, f"welcome message to {phone}")

    def send_reaction(self, key: dict[str, Any], emoji: str) -> bool:
        """React to one message (identified by its key) with `emoji`. True if accepted."""
        body = {"key": key, "reaction": emoji}
        return self._post("sendReaction", body, f"reaction to message {key.get('id')}")

    def _post(self, endpoint: str, body: dict[str, Any], what: str) -> bool:
        return self._post_reporting_failure(endpoint, body, what) is None

    def _post_reporting_failure(self, endpoint: str, body: dict[str, Any], what: str) -> str | None:
        """POST one message; None if Evolution accepted it, else the reason (also logged)."""
        try:
            response = self._http.post(
                f"{self._base_url}/message/{endpoint}/{self._instance}",
                json=body,
                headers={"apikey": self._api_key},
            )
        except httpx.HTTPError as error:
            # Some httpx errors have no message (a bare timeout): name the kind then.
            reason = str(error) or type(error).__name__
            logger.warning("%s failed: %s", what, reason)
            return reason
        if response.is_error:
            reason = f"HTTP {response.status_code} {response.text[:200]}".strip()
            logger.warning("%s failed: %s", what, reason)
            return reason
        return None

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

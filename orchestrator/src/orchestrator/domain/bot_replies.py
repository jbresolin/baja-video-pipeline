"""How the bot answers one video: a reaction on it, and any text as a quoted reply.

Volunteers send several files in quick succession, so loose messages were
confusing ("which video was that about?"). Every answer is now attached to
the video it is about:

* stored and verified        → ✅ reaction, no text;
* low_res or inbox nearly full → ⚠️ reaction + the text, quoting the video;
* refused or download failed → ❌ reaction + the text, quoting the video.

WhatsApp keeps one reaction per message and sender, so a later reaction
replaces an earlier one (⚠️ at the webhook, then ❌ if the download fails).

Reactions address the *message*: its key exactly as the webhook carried it
(an `@lid` remoteJid stays `@lid`). Texts address the *person*: the phone
JID (`EvolutionMessageKey.sender_jid`), as intake does since a519a91.
"""

from dataclasses import dataclass, field
from typing import Any

from orchestrator.clients.evolution import EvolutionClient, message_key
from shared.models import IncomingMedia, ReceivedAs

REACTION_STORED = "✅"
REACTION_WARNING = "⚠️"
REACTION_FAILED = "❌"


@dataclass(frozen=True)
class VideoMessageRef:
    """Which WhatsApp message a reply is about, and where texts go."""

    # data.key.remoteJid and data.key.id as they arrived.
    remote_jid: str
    message_id: str
    # The phone JID texts are sent to.
    reply_jid: str
    # A small copy of the message for Evolution's `quoted.message`: enough
    # for WhatsApp to draw the "reply to" preview (file name, type).
    quoted_message: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> dict[str, Any]:
        return message_key(self.remote_jid, self.message_id)

    @property
    def quoted(self) -> dict[str, Any]:
        return {"key": self.key, "message": self.quoted_message}


def quoted_message_for(incoming: IncomingMedia) -> dict[str, Any]:
    """The minimal message body to quote: type, mimetype and file name only.

    Not the whole webhook message: that carries media keys and a base64
    thumbnail we have no reason to send back.
    """
    media = incoming.media
    if incoming.received_as == ReceivedAs.VIDEO:
        return {"videoMessage": {"mimetype": media.mimetype or "video/mp4"}}
    document: dict[str, Any] = {"mimetype": media.mimetype or "video/mp4"}
    if media.file_name:
        document["fileName"] = media.file_name
    return {"documentMessage": document}


@dataclass(frozen=True)
class BotReply:
    """One answer to one video: a reaction and zero or more quoted texts."""

    target: VideoMessageRef
    reaction: str
    texts: tuple[str, ...] = ()


def send_bot_reply(evolution: EvolutionClient, reply: BotReply) -> None:
    """Reaction first (seen at a glance), then each text as a quoted reply."""
    evolution.send_reaction(reply.target.key, reply.reaction)
    for text in reply.texts:
        evolution.send_text(reply.target.reply_jid, text, quoted=reply.target.quoted)

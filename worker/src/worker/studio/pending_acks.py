"""`pending_acks.json`: delivered ACKs the studio still owes the orchestrator.

Design §17: the studio persists pending POST /jobs/{id}/delivered ACKs and
replays them after a restart. Without this, a studio restarted between
storing a pushed clip and ACKing it would leave the pushing worker to
upload the same clip to the outbox, and the studio to pull it again.

The file sits in the studio's working folder (beside worker.json by
default) and holds a JSON list, oldest first:

    [{"job_id": "…", "relative_path": "enduro/42/3.mp4",
      "sha256": "…", "size_bytes": 123, "in_playlist": true}]

It is rewritten whole after every change, through a temporary file and a
rename (worker.config.write_text_atomically), so a crash leaves either the
old list or the new one. An unreadable file is set aside as
"pending_acks.json.unreadable" and the studio starts with an empty list.
"""

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from worker.config import write_text_atomically

logger = logging.getLogger(__name__)

PENDING_ACKS_FILE_NAME = "pending_acks.json"


@dataclass
class PendingDelivery:
    job_id: str
    relative_path: str
    sha256: str
    size_bytes: int
    # Set once the clip is in latest.m3u, so an ACK retry (or a replay after
    # a restart) does not move it back above clips that arrived meanwhile.
    in_playlist: bool = False


class PendingAckFile:
    """Loads and saves the pending list. Not thread-safe: the caller locks."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> list[PendingDelivery]:
        if not self.path.exists():
            return []
        try:
            items = json.loads(self.path.read_text(encoding="utf-8"))
            return [PendingDelivery(**item) for item in items]
        except (ValueError, TypeError) as error:
            aside = self.path.with_name(self.path.name + ".unreadable")
            logger.error("%s is unreadable (%s); moved to %s", self.path, error, aside.name)
            self.path.replace(aside)
            return []

    def save(self, deliveries: list[PendingDelivery]) -> None:
        text = json.dumps([asdict(delivery) for delivery in deliveries], indent=1) + "\n"
        write_text_atomically(self.path, text)

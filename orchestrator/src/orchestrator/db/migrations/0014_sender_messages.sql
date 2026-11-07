-- LIVESTACK-49: reviewer context, the sender's messages.
--
-- Filmmakers write what the reviewer needs next to the video ("carro 42,
-- segunda passagem", "errei, é a 3"). Every private message that is not a
-- video, from a contact whose videos would be accepted, is kept here for
-- the job page's "Mensagens de <remetente>" section
-- (domain/sender_messages.py). No media is downloaded: only text, captions,
-- and the kind of anything else. Deleted after the Settings value
-- sender_messages_keep_days (14 by default).
CREATE TABLE sender_messages (
    -- WhatsApp message id (Evolution data.key.id).
    message_id        TEXT PRIMARY KEY,
    -- Like jobs.sender_jid: the phone JID, also for "@lid" senders.
    sender_jid        TEXT NOT NULL,
    -- When the webhook arrived (videos are ordered by the same clock).
    received_at       TEXT NOT NULL,
    -- WhatsApp's messageTimestamp, when it had one.
    sent_at           TEXT,
    -- shared.models.SenderMessageKind: text, image, audio, sticker, ...
    kind              TEXT NOT NULL,
    -- The text or caption. NULL for media without a caption, and once the
    -- sender deleted the message for everyone.
    text              TEXT,
    -- The message this one quotes (contextInfo.stanzaId), when it is a reply.
    quoted_message_id TEXT,
    edited_at         TEXT,
    deleted_at        TEXT
);

-- One sender's messages in arrival order; replies by what they quote;
-- the retention sweep by age.
CREATE INDEX sender_messages_by_sender ON sender_messages (sender_jid, received_at);
CREATE INDEX sender_messages_by_quoted ON sender_messages (quoted_message_id);
CREATE INDEX sender_messages_by_received_at ON sender_messages (received_at);

-- The video's own caption, kept with its job (not subject to the retention).
ALTER TABLE jobs ADD COLUMN caption TEXT;

-- One sender's videos in arrival order: the boundaries between "after this
-- video" and "before the next one".
CREATE INDEX jobs_by_sender ON jobs (sender_jid, received_at);

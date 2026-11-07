-- LIVESTACK-44: welcome message for new contacts.
--
-- When Evolution last accepted the welcome message for this contact, shown
-- on the admin Contacts page. NULL if it never did. Every attempt, failed
-- ones included, is also an event (welcome_sent / welcome_failed).
-- The message itself is an admin setting (app_settings, key
-- welcome_message), so it needs no column.
ALTER TABLE contacts ADD COLUMN welcome_sent_at TEXT;

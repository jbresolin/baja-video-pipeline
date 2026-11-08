-- LIVESTACK-51 item 2: one contact per phone, whichever way the number
-- was written (domain/phones.py).
--
-- On the 2026-11-07 test the admin typed a filmmaker's number as people
-- write it today, with the mobile 9th digit (55 DDD 9xxxx-xxxx), and gave
-- it a default prova. WhatsApp's JID for that older number has no 9th
-- digit (55 DDD xxxx-xxxx), so the videos were recorded under a second
-- contact without a default prova, and the jobs got none. Another contact
-- had been typed without the country code (DDD 9xxxx-xxxx).
--
-- From now on every number is kept in one form: a Brazilian mobile as
-- 55 + DDD + 9 + 8 digits; a number typed without the country code
-- (DDD + 8 or 9 digits) gets 55 in front. This migration rewrites the
-- existing rows the same way and merges the ones that turn out to be the
-- same phone:
--   * status, display name, default prova and notes (what the admin edits)
--     come from the row the admin worked on: a status other than "unknown"
--     first, then the most recently changed; a field empty there is taken
--     from the other row;
--   * the WhatsApp name from the row that wrote last; first seen = the
--     earliest, last seen and the last replies = the latest.
-- Each merge is written to the admin log ("contact_merged", Contatos page).
-- Jobs and messages keep their sender JID as it came; they are matched to
-- contacts through the same rule.

-- The rule, in SQL: the phone with 55 added when typed without it...
CREATE TEMP TABLE contact_with_country AS
SELECT
    contacts.*,
    CASE
        WHEN length(phone) = 11 AND substr(phone, 1, 1) <> '0' AND substr(phone, 2, 1) <> '0'
             AND substr(phone, 3, 1) = '9' THEN '55' || phone
        WHEN length(phone) = 10 AND substr(phone, 1, 1) <> '0' AND substr(phone, 2, 1) <> '0'
             AND substr(phone, 3, 1) BETWEEN '2' AND '9' THEN '55' || phone
        ELSE phone
    END AS with_country
FROM contacts;

-- ...then the 9th digit added to a Brazilian mobile written without it
-- (a mobile's 8-digit number starts with 6 to 9; a landline's with 2 to 5).
CREATE TEMP TABLE contact_keys AS
SELECT
    contact_with_country.*,
    CASE
        WHEN length(with_country) = 12 AND substr(with_country, 1, 2) = '55'
             AND substr(with_country, 5, 1) BETWEEN '6' AND '9'
        THEN substr(with_country, 1, 4) || '9' || substr(with_country, 5)
        ELSE with_country
    END AS canonical
FROM contact_with_country;

-- Within each phone, the row the admin worked on first.
CREATE TEMP TABLE ranked_contacts AS
SELECT
    contact_keys.*,
    ROW_NUMBER() OVER (
        PARTITION BY canonical
        ORDER BY (status <> 'unknown') DESC, updated_at DESC, phone
    ) AS admin_rank
FROM contact_keys;

-- One merged row per phone that has more than one row, or whose only row
-- is not in the canonical form yet.
CREATE TEMP TABLE merged_contacts AS
SELECT
    canonical AS phone,
    (SELECT r.display_name FROM ranked_contacts r WHERE r.canonical = g.canonical
        AND r.display_name IS NOT NULL AND r.display_name <> '' ORDER BY r.admin_rank LIMIT 1)
        AS display_name,
    (SELECT r.push_name FROM ranked_contacts r WHERE r.canonical = g.canonical
        AND r.push_name IS NOT NULL ORDER BY r.last_seen_at DESC LIMIT 1) AS push_name,
    (SELECT r.status FROM ranked_contacts r WHERE r.canonical = g.canonical
        ORDER BY r.admin_rank LIMIT 1) AS status,
    (SELECT r.default_prova_id FROM ranked_contacts r WHERE r.canonical = g.canonical
        AND r.default_prova_id IS NOT NULL ORDER BY r.admin_rank LIMIT 1) AS default_prova_id,
    (SELECT r.notes FROM ranked_contacts r WHERE r.canonical = g.canonical
        AND r.notes IS NOT NULL AND r.notes <> '' ORDER BY r.admin_rank LIMIT 1) AS notes,
    MIN(first_seen_at) AS first_seen_at,
    MAX(last_seen_at) AS last_seen_at,
    MAX(last_refusal_reply_at) AS last_refusal_reply_at,
    MIN(created_at) AS created_at,
    MAX(updated_at) AS updated_at,
    MAX(welcome_sent_at) AS welcome_sent_at,
    group_concat(g.phone, ', ') AS old_phones
FROM ranked_contacts g
GROUP BY canonical
HAVING COUNT(*) > 1 OR MAX(g.phone <> canonical) = 1;

DELETE FROM contacts
WHERE phone IN (
    SELECT phone FROM ranked_contacts WHERE canonical IN (SELECT phone FROM merged_contacts)
);

INSERT INTO contacts (
    phone, display_name, push_name, status, default_prova_id, notes, first_seen_at,
    last_seen_at, last_refusal_reply_at, created_at, updated_at, welcome_sent_at
)
SELECT
    phone, display_name, push_name, status, default_prova_id, notes, first_seen_at,
    last_seen_at, last_refusal_reply_at, created_at, updated_at, welcome_sent_at
FROM merged_contacts;

INSERT INTO events (at, actor, kind, reason, details_json)
SELECT
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
    'system',
    'contact_merged',
    phone || ': one contact for ' || old_phones,
    json_object('before', json_object('phones', old_phones), 'after', json_object('phone', phone))
FROM merged_contacts;

DROP TABLE contact_with_country;
DROP TABLE contact_keys;
DROP TABLE ranked_contacts;
DROP TABLE merged_contacts;

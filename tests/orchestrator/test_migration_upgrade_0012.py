"""Upgrading the database from schema 11 to 12 (LIVESTACK-44, welcome message).

Main (with LIVESTACK-45's 0011) leaves a database at version 11. 0012 adds
`contacts.welcome_sent_at`: when Evolution last accepted the welcome
message for that contact.

The test builds a database as main leaves it, with one contact, opens it
with this code (as the orchestrator does at startup) and checks that only
0012 runs, the contact survives, and it starts as "never sent": nobody
knows whether João told those people about the bot by hand.
"""

import sqlite3

from orchestrator.db.database import Database, list_migrations
from orchestrator.testing.harness import build_harness

SCHEMA_ON_MAIN = 11
WHEN = "2026-11-06T12:00:00.000000Z"
PHONE = "5511987654321"


def database_at_schema_11(data_dir):
    path = data_dir / "db" / "bvp.sqlite3"
    path.parent.mkdir(parents=True)
    connection = sqlite3.connect(path)
    for number, _, sql in list_migrations()[:SCHEMA_ON_MAIN]:
        connection.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
    # A filmmaker the admin added (allowed) before this upgrade.
    connection.execute(
        "INSERT INTO contacts (phone, display_name, status, created_at, updated_at)"
        " VALUES (?, 'Conceição Araújo', 'allowed', ?, ?)",
        (PHONE, WHEN, WHEN),
    )
    connection.commit()
    connection.close()


def test_only_0012_runs_and_old_contacts_were_never_welcomed(tmp_path):
    database_at_schema_11(tmp_path)

    database = Database(tmp_path / "db" / "bvp.sqlite3")
    applied = database.apply_migrations()
    assert applied == [name for number, name, _ in list_migrations() if number > SCHEMA_ON_MAIN]
    assert applied[0] == "0012_welcome_message.sql"

    with database.transaction() as connection:
        contact = connection.execute("SELECT * FROM contacts").fetchone()
    # The contact is untouched, and has no "last sent" date yet.
    assert (contact["phone"], contact["display_name"], contact["status"]) == (
        PHONE,
        "Conceição Araújo",
        "allowed",
    )
    assert contact["welcome_sent_at"] is None
    database.close()


def test_an_upgraded_contact_can_be_welcomed(tmp_path):
    # The orchestrator opens the upgraded database (as it does at startup)
    # and the admin presses "Enviar boas-vindas" on the old contact's row.
    database_at_schema_11(tmp_path / "data")
    harness = build_harness(tmp_path / "data")
    try:
        response = harness.client.post(f"/admin/contacts/{PHONE}/welcome", auth=harness.admin_auth)
        assert response.status_code == 200, response.text
        assert response.json()["contact"]["welcome_sent_at"] is not None
        [text] = harness.evolution.texts_to(PHONE)
        assert text.startswith("Olá Conceição Araújo! 👋")
    finally:
        harness.client.close()
        harness.context.database.close()

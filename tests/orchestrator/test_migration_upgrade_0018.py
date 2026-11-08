"""Migration 0018: one contact per phone (LIVESTACK-51 item 2).

Before this change the same phone could be two contacts: the row the admin
typed (with the mobile 9th digit, or without the country code) and the row
intake recorded from WhatsApp's id (without the 9th digit). 0018 rewrites
every number in the one form (domain/phones.py) and merges the rows that
turn out to be the same phone.

The test builds a database at schema 17 (0016 is LIVESTACK-48's clock,
0017 is this branch's duplicate videos), with the shapes seen on the
production data of 2026-11-07, runs 0018, and checks the merged rows.
"""

import sqlite3

from orchestrator.db.database import Database, list_migrations

SCHEMA_BEFORE = 17


def add_contact(connection, phone, status, updated_at, **fields):
    columns = {
        "phone": phone,
        "status": status,
        "created_at": "2026-11-07T02:00:00.000000Z",
        "updated_at": updated_at,
        **fields,
    }
    names = ", ".join(columns)
    marks = ", ".join("?" for _ in columns)
    connection.execute(f"INSERT INTO contacts ({names}) VALUES ({marks})", list(columns.values()))


def migrated(tmp_path):
    path = tmp_path / "db" / "bvp.sqlite3"
    path.parent.mkdir(parents=True)
    connection = sqlite3.connect(path)
    for number, _, sql in list_migrations()[:SCHEMA_BEFORE]:
        connection.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
    connection.execute(
        "INSERT INTO provas (id, name, slug, display_order, created_at, updated_at)"
        " VALUES (20, 'Tração', 'tracao', 1, '2026-11-07T00:00:00.000000Z',"
        " '2026-11-07T00:00:00.000000Z')"
    )
    # Pedro as the admin typed him (with the 9th digit, a default prova)...
    add_contact(
        connection,
        "5511987650000",
        "allowed",
        "2026-11-07T18:55:19.000000Z",
        display_name="Pedro",
        default_prova_id=20,
    )
    # ...and as intake recorded him a minute later from the JID: unknown,
    # his WhatsApp name, his first and last messages, a note left there.
    add_contact(
        connection,
        "551187650000",
        "unknown",
        "2026-11-07T18:58:29.000000Z",
        display_name="Pedro S.",
        push_name="Pedro S.",
        notes="coringa",
        first_seen_at="2026-11-07T18:56:27.000000Z",
        last_seen_at="2026-11-07T18:58:29.000000Z",
    )
    # Maria (filmagem), typed without the country code, never merged before.
    add_contact(
        connection, "19981234567", "allowed", "2026-11-07T03:00:00.000000Z", display_name="Maria"
    )
    # A São Paulo landline: already in its one form, nothing to do.
    add_contact(connection, "551134567890", "blocked", "2026-11-07T03:00:00.000000Z")
    connection.commit()
    connection.close()
    database = Database(path)
    applied = database.apply_migrations()
    return database, applied


def test_0018_merges_the_two_forms_of_one_phone(tmp_path):
    database, applied = migrated(tmp_path)

    assert applied[0] == "0018_one_contact_per_phone.sql"
    with database.transaction() as connection:
        rows = {row["phone"]: row for row in connection.execute("SELECT * FROM contacts")}
        merges = connection.execute(
            "SELECT reason FROM events WHERE kind = 'contact_merged' ORDER BY reason"
        ).fetchall()
    database.close()

    # One Pedro, under the number with the 9th digit.
    assert sorted(rows) == ["551134567890", "5511987650000", "5519981234567"]
    pedro = rows["5511987650000"]
    # What the admin set wins (status, name, default prova)...
    assert pedro["status"] == "allowed"
    assert pedro["display_name"] == "Pedro"
    assert pedro["default_prova_id"] == 20
    # ...a field empty there comes from the other row...
    assert pedro["notes"] == "coringa"
    # ...and what intake saw is kept.
    assert pedro["push_name"] == "Pedro S."
    assert pedro["first_seen_at"] == "2026-11-07T18:56:27.000000Z"
    assert pedro["last_seen_at"] == "2026-11-07T18:58:29.000000Z"
    # Maria got her country code; the landline is untouched.
    assert rows["5519981234567"]["display_name"] == "Maria"
    assert rows["551134567890"]["status"] == "blocked"
    # Each rewrite is in the admin log (Contatos, "Últimas alterações").
    assert [merge["reason"] for merge in merges] == [
        "5511987650000: one contact for 5511987650000, 551187650000",
        "5519981234567: one contact for 19981234567",
    ]


def test_when_the_admin_changed_both_rows_the_latest_change_wins(tmp_path):
    # As in production: "Teste" typed without the country code, then again
    # with it, then the first row blocked a minute later. Both rows carry
    # an admin's decision; the later one (blocked) is kept.
    path = tmp_path / "db" / "bvp.sqlite3"
    path.parent.mkdir(parents=True)
    connection = sqlite3.connect(path)
    for number, _, sql in list_migrations()[:SCHEMA_BEFORE]:
        connection.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
    add_contact(connection, "11912340000", "blocked", "2026-11-07T02:30:27.000000Z")
    add_contact(connection, "5511912340000", "allowed", "2026-11-07T02:29:35.000000Z")
    connection.commit()
    connection.close()
    database = Database(path)
    database.apply_migrations()

    with database.transaction() as connection:
        rows = connection.execute("SELECT phone, status FROM contacts").fetchall()
    database.close()

    assert [tuple(row) for row in rows] == [("5511912340000", "blocked")]

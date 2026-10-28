"""Tests for encrypted-at-rest `db_password` storage (credential_crypto.py),
the lazy migration of pre-existing clear-text rows (registry.py), and the
`.omsecrets` file projection + drift detection (omsecrets_store.py).

Design context: docs/design-notes/exchange/REPLY-DATABASE-CREDENTIAL-CAPABILITY-
VISIBILITY.md §7.
"""
from __future__ import annotations

import sqlite3

import pytest

from resource_explorer import credential_crypto
from resource_explorer import omsecrets_store
from resource_explorer.registry import DatabaseEntity, ProjectRegistry


@pytest.fixture(autouse=True)
def _reset_crypto_cache(monkeypatch):
    """Give every test a deterministic, isolated encryption key so tests
    don't depend on (or collide over) the real process environment."""
    monkeypatch.setenv("RE_DB_CREDENTIAL_KEY", "test-key-for-credential-encryption")
    credential_crypto.reset_credential_key_cache()
    yield
    credential_crypto.reset_credential_key_cache()


class TestEncryptionRoundTrip:
    def test_encrypt_then_decrypt_returns_original_plaintext(self):
        plaintext = "s3cr3t-p@ssword"
        stored = credential_crypto.encrypt_db_password(plaintext)
        assert stored != plaintext
        assert credential_crypto.is_encrypted(stored)
        assert credential_crypto.decrypt_db_password(stored) == plaintext

    def test_empty_password_stays_empty(self):
        assert credential_crypto.encrypt_db_password("") == ""
        assert credential_crypto.decrypt_db_password("") == ""

    def test_wrong_key_raises_rather_than_returning_garbage(self, monkeypatch):
        stored = credential_crypto.encrypt_db_password("hunter2")
        monkeypatch.setenv("RE_DB_CREDENTIAL_KEY", "a-completely-different-key")
        credential_crypto.reset_credential_key_cache()
        with pytest.raises(ValueError):
            credential_crypto.decrypt_db_password(stored)

    def test_registry_round_trip_via_register_and_get(self, tmp_path):
        registry = ProjectRegistry(db_path=str(tmp_path / "reg.db"))
        registry.register_database(DatabaseEntity(
            slug="rtdb", display_name="RT DB", db_type="postgresql",
            host="localhost", port=5432, database_name="rtdb",
            db_user="egeria_user", db_password="my-plain-password",
        ))
        fetched = registry.get_database("rtdb")
        assert fetched.db_password == "my-plain-password"

        # And the column on disk is genuinely not clear text.
        with sqlite3.connect(str(tmp_path / "reg.db")) as conn:
            raw = conn.execute(
                "SELECT db_password FROM databases WHERE slug = 'rtdb'"
            ).fetchone()[0]
        assert raw != "my-plain-password"
        assert credential_crypto.is_encrypted(raw)

    def test_update_database_credentials_also_encrypts(self, tmp_path):
        registry = ProjectRegistry(db_path=str(tmp_path / "reg.db"))
        registry.register_database(DatabaseEntity(
            slug="upd", display_name="Upd DB", db_type="postgresql",
            host="localhost", port=5432, database_name="upd",
            db_user="old_user", db_password="old-pass",
        ))
        registry.update_database_credentials("upd", "new_user", "new-pass")
        fetched = registry.get_database("upd")
        assert fetched.db_user == "new_user"
        assert fetched.db_password == "new-pass"
        with sqlite3.connect(str(tmp_path / "reg.db")) as conn:
            raw = conn.execute(
                "SELECT db_password FROM databases WHERE slug = 'upd'"
            ).fetchone()[0]
        assert credential_crypto.is_encrypted(raw)


class TestLazyMigration:
    def test_pre_existing_clear_text_row_is_readable_and_migrated_on_read(self, tmp_path):
        db_path = str(tmp_path / "reg.db")
        registry = ProjectRegistry(db_path=db_path)
        # Register normally (this encrypts), then reach around the API to
        # simulate a row written before encryption shipped by overwriting
        # the column with plain text directly.
        registry.register_database(DatabaseEntity(
            slug="legacy", display_name="Legacy DB", db_type="postgresql",
            host="localhost", port=5432, database_name="legacy",
            db_user="egeria_user", db_password="placeholder",
        ))
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE databases SET db_password = ? WHERE slug = 'legacy'",
                ("clear-text-legacy-password",),
            )
            conn.commit()

        # First read: must transparently return the clear-text value, not
        # try (and fail) to decrypt it as if it were ciphertext.
        fetched = registry.get_database("legacy")
        assert fetched.db_password == "clear-text-legacy-password"

        # And it must have been re-encrypted in place as a side effect.
        with sqlite3.connect(db_path) as conn:
            raw = conn.execute(
                "SELECT db_password FROM databases WHERE slug = 'legacy'"
            ).fetchone()[0]
        assert credential_crypto.is_encrypted(raw)

        # Second read still returns the original plaintext, now via the
        # normal decrypt path rather than the migration path.
        fetched_again = registry.get_database("legacy")
        assert fetched_again.db_password == "clear-text-legacy-password"


class TestOmsecretsStore:
    def test_write_credential_is_noop_when_no_path_configured(self):
        wrote = omsecrets_store.write_credential(
            "somedb::PostgreSQL Secret", "user", "pass", path=""
        )
        assert wrote is False

    def test_write_and_read_back_matches_real_file_shape(self, tmp_path):
        target = tmp_path / "resource-explorer.omsecrets"
        wrote = omsecrets_store.write_credential(
            "localhost_docker_coco_pharma::PostgreSQL Secret",
            "surveyor", "surveyor4egeria",
            path=str(target),
        )
        assert wrote is True
        assert target.exists()

        import yaml
        data = yaml.safe_load(target.read_text())
        entry = data["secretsCollections"]["localhost_docker_coco_pharma::PostgreSQL Secret"]
        assert entry["displayName"] == "localhost_docker_coco_pharma::PostgreSQL Secret"
        assert entry["refreshTimeInterval"] == 60
        assert entry["secrets"]["userId"] == "surveyor"
        assert entry["secrets"]["clearPassword"] == "surveyor4egeria"

    def test_write_preserves_other_existing_collections(self, tmp_path):
        target = tmp_path / "resource-explorer.omsecrets"
        omsecrets_store.write_credential("db_one::PostgreSQL Secret", "u1", "p1", path=str(target))
        omsecrets_store.write_credential("db_two::PostgreSQL Secret", "u2", "p2", path=str(target))
        names = omsecrets_store.collection_names(path=str(target))
        assert names == {"db_one::PostgreSQL Secret", "db_two::PostgreSQL Secret"}

    def test_collection_names_empty_when_file_missing(self, tmp_path):
        assert omsecrets_store.collection_names(path=str(tmp_path / "nope.omsecrets")) == set()

    def test_secrets_collection_name_convention(self):
        assert (
            omsecrets_store.secrets_collection_name("localhost_docker_coco_pharma")
            == "localhost_docker_coco_pharma::PostgreSQL Secret"
        )


class TestCredentialDrift:
    def test_agreeing_case_both_sides_have_the_collection(self, tmp_path, monkeypatch):
        registry = ProjectRegistry(db_path=str(tmp_path / "reg.db"))
        registry.register_database(DatabaseEntity(
            slug="agree_db", display_name="Agree DB", db_type="postgresql",
            host="localhost", port=5432, database_name="agree_db",
            db_user="surveyor", db_password="agree-pass",
        ))
        omsecrets_path = tmp_path / "resource-explorer.omsecrets"
        omsecrets_store.write_credential(
            "agree_db::PostgreSQL Secret", "surveyor", "agree-pass",
            path=str(omsecrets_path),
        )
        monkeypatch.setattr(omsecrets_store, "local_path", lambda: str(omsecrets_path))

        result = registry.check_credential_drift("agree_db")
        assert result["in_registry"] is True
        assert result["in_omsecrets"] is True
        assert result["omsecrets_configured"] is True
        assert result["in_sync"] is True

    def test_disagreeing_case_registry_has_it_omsecrets_does_not(self, tmp_path, monkeypatch):
        registry = ProjectRegistry(db_path=str(tmp_path / "reg.db"))
        registry.register_database(DatabaseEntity(
            slug="drift_db", display_name="Drift DB", db_type="postgresql",
            host="localhost", port=5432, database_name="drift_db",
            db_user="surveyor", db_password="drift-pass",
        ))
        omsecrets_path = tmp_path / "resource-explorer.omsecrets"
        # Write a DIFFERENT collection, so drift_db's is absent.
        omsecrets_store.write_credential(
            "other_db::PostgreSQL Secret", "u", "p", path=str(omsecrets_path),
        )
        monkeypatch.setattr(omsecrets_store, "local_path", lambda: str(omsecrets_path))

        result = registry.check_credential_drift("drift_db")
        assert result["in_registry"] is True
        assert result["in_omsecrets"] is False
        assert result["in_sync"] is False

    def test_unconfigured_omsecrets_path_reports_not_checked_not_false_sync(self, tmp_path, monkeypatch):
        registry = ProjectRegistry(db_path=str(tmp_path / "reg.db"))
        registry.register_database(DatabaseEntity(
            slug="noconf_db", display_name="No Conf DB", db_type="postgresql",
            host="localhost", port=5432, database_name="noconf_db",
            db_user="surveyor", db_password="pass",
        ))
        monkeypatch.setattr(omsecrets_store, "local_path", lambda: "")

        result = registry.check_credential_drift("noconf_db")
        assert result["omsecrets_configured"] is False
        # find-absence-as-answer: "never checked" must not collapse into
        # "checked and it disagreed" (False) — it's a distinct None.
        assert result["in_sync"] is None

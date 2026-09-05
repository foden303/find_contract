import asyncio
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from core.cache import Cache
from core.config import Config
from core.migration import import_legacy
from core.paths import data_dir, state_dir, upload_dir
from core.search import SearchClient, SearchError
from core.settings import effective_settings, public_settings, save_settings
from core.store import Store


class IsolatedProfile(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="finder-test-")
        self.addCleanup(self.temporary.cleanup)
        environment = {key: value for key, value in os.environ.items() if not key.startswith("FINDER_")}
        environment["FINDER_HOME"] = str(Path(self.temporary.name) / "Người dùng có dấu")
        self.environment = patch.dict(os.environ, environment, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_saved_environment_and_explicit_precedence_without_secret_disclosure(self):
        save_settings({"search_provider": "brave", "search_api_key": "private-test-key"})
        self.assertEqual(Config().search_provider, "brave")
        self.assertNotIn("private-test-key", json.dumps(public_settings()))
        with patch.dict(os.environ, {"FINDER_SEARCH_PROVIDER": "searxng", "FINDER_SEARCH_API_KEY": "env-key"}):
            self.assertEqual(Config().search_provider, "searxng")
            self.assertEqual(Config(search_provider="ddg").search_provider, "ddg")
            save_settings({"search_provider": "serper", "search_api_key": "ignored"})
            self.assertEqual(effective_settings()["search_api_key"], "env-key")
        self.assertEqual(Config().search_api_key, "private-test-key")
        save_settings({"search_api_key": ""})
        self.assertFalse(public_settings()["has_api_key"])
        self.assertIsNone(Config().search_api_key)
        if os.name == "nt":
            save_settings({"search_api_key": "dpapi-only-secret"})
            self.assertNotIn("dpapi-only-secret", (Path(state_dir()) / "settings.json").read_text())
            self.assertEqual(Config().search_api_key, "dpapi-only-secret")

    def test_import_snapshots_committed_wal_and_refuses_destination_overwrite(self):
        source = Path(self.temporary.name) / "old source"
        source.mkdir()
        old = Store(str(source / ".runs.db"))
        self.addCleanup(old.close)
        run_id = old.create_run("Preserve WAL", "fixture", 0, {})
        old.finish_run(run_id, "done", 0)
        self.assertTrue(Path(str(source / ".runs.db") + "-wal").exists())
        (source / ".uploads").mkdir()
        (source / ".uploads" / "dữ liệu.csv").write_text("Company\nExample\n", encoding="utf-8")
        imported = import_legacy(str(source))
        self.assertEqual(set(imported), {".runs.db", ".uploads"})
        current = Store(str(Path(data_dir()) / ".runs.db"))
        try:
            self.assertEqual(current.get_run(run_id)["label"], "Preserve WAL")
        finally:
            current.close()
        self.assertEqual((Path(upload_dir()) / "dữ liệu.csv").read_text(encoding="utf-8"), "Company\nExample\n")
        with self.assertRaises(ValueError):
            import_legacy(str(source))
        self.assertEqual(old.get_run(run_id)["status"], "done")

    def test_import_rejects_corrupt_source_without_losing_destination(self):
        source = Path(self.temporary.name) / "broken"
        source.mkdir()
        (source / ".runs.db").write_bytes(b"not sqlite")
        destination = Path(data_dir()) / ".runs.db"
        current = Store(str(destination))
        current.close()
        with self.assertRaises((ValueError, sqlite3.DatabaseError)):
            import_legacy(str(source))
        current = Store(str(destination))
        try:
            self.assertEqual(current.list_runs(), [])
        finally:
            current.close()
        self.assertEqual((source / ".runs.db").read_bytes(), b"not sqlite")

    def test_backend_failure_is_not_cached_as_no_results(self):
        async def scenario():
            config = Config(search_provider="ddg")
            cache = Cache(config.cache_path, config.cache_ttl)
            client = SearchClient(config, cache)
            try:
                with patch.object(client, "_run_backend", AsyncMock(side_effect=ConnectionError("offline"))), patch("core.search.MAX_ATTEMPTS", 1):
                    with self.assertRaises(SearchError):
                        await client.search("Runtime failure fixture")
                with patch.object(client, "_run_backend", AsyncMock(return_value=[{"href": "https://example.com", "title": "Recovered"}])):
                    results = await client.search("Runtime failure fixture")
                    self.assertEqual(results[0].title, "Recovered")
            finally:
                await client.aclose()
                cache.close()
        asyncio.run(scenario())

    def test_desktop_auth_origin_guard_and_restart_history(self):
        from fastapi.testclient import TestClient
        import web.app as application

        with patch.dict(os.environ, {"FINDER_SESSION_TOKEN": "session-test"}):
            with TestClient(application.app, base_url="http://127.0.0.1") as client:
                self.assertEqual(client.get("/api/settings").status_code, 401)
                response = client.get("/?token=session-test")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(str(response.url), "http://127.0.0.1/")
                self.assertEqual(client.get("/api/settings").status_code, 200)
                self.assertEqual(client.put("/api/settings", headers={"Origin": "https://evil.invalid"}, json={}).status_code, 403)
                self.assertEqual(client.get("/api/runs", headers={"Host": "evil.invalid"}).status_code, 403)
                run_id = application.store.create_run("Interrupted", "fixture", 1, {})
            with TestClient(application.app, base_url="http://127.0.0.1", headers={"X-Finder-Token": "session-test"}) as client:
                row = client.get(f"/api/runs/{run_id}").json()["run"]
                self.assertEqual(row["status"], "interrupted")


if __name__ == "__main__":
    unittest.main()

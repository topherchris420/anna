from flask import url_for

from lib.test import ViewTestMixin


class TestUp(ViewTestMixin):
    def test_up(self):
        """Up should respond with a success 200."""
        response = self.client.get(url_for("up.index"))

        assert response.status_code == 200

    def test_up_databases(self, monkeypatch):
        """Up databases should respond with a success 200 and probe its dependencies."""
        calls = {"redis_ping": 0, "db_query": 0}

        def fake_ping():
            calls["redis_ping"] += 1

        def fake_execute(_query):
            calls["db_query"] += 1

        monkeypatch.setattr("allthethings.up.views.redis.ping", fake_ping)
        monkeypatch.setattr("allthethings.up.views.db.session.execute", fake_execute)

        response = self.client.get(url_for("up.databases"))

        assert response.status_code == 200
        assert calls == {"redis_ping": 1, "db_query": 1}

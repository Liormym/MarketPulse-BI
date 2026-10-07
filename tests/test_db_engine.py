from marketpulse.load import db as db_module


def test_engine_pings_pooled_connections_before_use(monkeypatch):
    captured = {}

    def spy_create_engine(url, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(db_module, "create_engine", spy_create_engine)

    db_module.get_engine.__wrapped__()

    assert captured.get("pool_pre_ping") is True

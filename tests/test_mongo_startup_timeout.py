def test_mongodb_allows_brief_primary_elections(monkeypatch):
    import pyUltroid.startup._database as database

    calls = []

    class FakeMongoClient:
        def __init__(self, uri, **kwargs):
            calls.append((uri, kwargs))

        def __getitem__(self, name):
            return object()

    monkeypatch.setattr(database, "MongoClient", FakeMongoClient)
    database.MongoDB("mongodb://example.invalid")

    assert calls == [
        (
            "mongodb://example.invalid",
            {"serverSelectionTimeoutMS": 30_000},
        )
    ]

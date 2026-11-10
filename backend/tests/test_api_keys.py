from datetime import UTC, datetime, timedelta

from burnrate import cli
from burnrate.models import ApiKey
from burnrate.security import create_api_key, list_api_keys, revoke_api_key

EVENT = {"provider": "openai", "model": "gpt-4o", "input_tokens": 10}


def _post(client, key):
    return client.post("/v1/events", json={"events": [EVENT]}, headers={"X-API-Key": key})


def _key(session, raw) -> ApiKey:
    session.expire_all()
    return next(k for k in list_api_keys(session) if raw.startswith(k.prefix))


def test_last_used_is_recorded_without_a_write_per_batch(client, session):
    raw = create_api_key(session, "svc")
    assert _key(session, raw).last_used_at is None

    assert _post(client, raw).status_code == 202
    first = _key(session, raw).last_used_at
    assert first is not None

    assert _post(client, raw).status_code == 202
    assert _key(session, raw).last_used_at == first  # within the resolution window: no write

    key = _key(session, raw)
    key.last_used_at = datetime.now(UTC) - timedelta(minutes=10)
    session.commit()
    assert _post(client, raw).status_code == 202
    assert _key(session, raw).last_used_at.replace(tzinfo=UTC) > datetime.now(UTC) - timedelta(minutes=1)


def test_revoked_key_is_rejected_on_the_next_request(client, session):
    raw = create_api_key(session, "leaky")
    other = create_api_key(session, "other")
    assert _post(client, raw).status_code == 202

    revoked = revoke_api_key(session, raw)  # by the raw key, as found in a leak
    assert revoked is not None and revoked.revoked_at is not None
    assert _post(client, raw).status_code == 401
    assert _post(client, other).status_code == 202  # other keys unaffected

    when = revoked.revoked_at
    assert revoke_api_key(session, str(revoked.id)).revoked_at == when  # by id; idempotent
    assert revoke_api_key(session, "999") is None
    assert revoke_api_key(session, "br_live_not-a-key") is None


def test_cli_list_and_revoke(engine, monkeypatch, capsys):
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(cli, "SessionLocal", Session)
    monkeypatch.setattr(cli, "init_db", lambda: None)

    assert cli.main(["list-keys"]) == 0
    assert "No API keys" in capsys.readouterr().out

    assert cli.main(["create-key", "billing-svc"]) == 0
    raw = capsys.readouterr().out.strip().splitlines()[-1]
    assert cli.main(["list-keys"]) == 0
    out = capsys.readouterr().out
    assert "billing-svc" in out and "last used never" in out and "active" in out and raw not in out

    assert cli.main(["revoke-key", "1"]) == 0
    assert "Revoked:" in capsys.readouterr().out
    assert cli.main(["list-keys"]) == 0
    assert "revoked 20" in capsys.readouterr().out

    assert cli.main(["revoke-key", "br_live_wrong"]) == 1
    err = capsys.readouterr().err
    assert "No matching key" in err and "br_live_wrong" not in err  # never echo a possible key

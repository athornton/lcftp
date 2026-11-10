from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lcftp.db.schema import DeviceTokenRow
from lcftp.stores.tokens import SqlTokenStore
from lcftp_auth.tokens import generate_token, hash_token
from tests.support.factories import make_observatory, make_user


async def test_token_lifecycle(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    await make_observatory(db_session, user)
    store = SqlTokenStore(db_session)
    raw = generate_token()
    created = await store.create("obs1", "pi", hash_token(raw))
    assert created.owner_login == "athornton"

    found = await store.lookup(hash_token(raw))
    assert found == created
    now = datetime.now(tz=UTC)
    await store.touch(created.id, now)
    assert (await store.list_for("obs1"))[0].last_used == now

    await store.revoke(created.id, now)
    revoked = await store.lookup(hash_token(raw))
    assert revoked is not None
    assert revoked.revoked_at == now


async def test_raw_token_is_never_stored(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    await make_observatory(db_session, user)
    raw = generate_token()
    await SqlTokenStore(db_session).create("obs1", "pi", hash_token(raw))
    rows = (await db_session.execute(select(DeviceTokenRow))).scalars().all()
    for row in rows:
        assert raw not in (row.token_hash, row.label)


async def test_lookup_unknown(db_session: AsyncSession) -> None:
    assert await SqlTokenStore(db_session).lookup("0" * 64) is None

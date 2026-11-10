from sqlalchemy.ext.asyncio import AsyncSession

from lcftp.stores.users import SqlUserStore


async def test_upsert_is_idempotent(db_session: AsyncSession) -> None:
    store = SqlUserStore(db_session)
    first = await store.upsert_github_user("athornton", 4242)
    again = await store.upsert_github_user("athornton", 4242)
    assert again.id == first.id
    assert await store.get(first.id) == first


async def test_upsert_updates_renamed_login(db_session: AsyncSession) -> None:
    store = SqlUserStore(db_session)
    first = await store.upsert_github_user("old-name", 4242)
    renamed = await store.upsert_github_user("new-name", 4242)
    assert renamed.id == first.id
    assert renamed.github_login == "new-name"


async def test_get_missing_user(db_session: AsyncSession) -> None:
    assert await SqlUserStore(db_session).get(999) is None

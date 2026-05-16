from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User


class UserRepository:
    """Async persistence operations for monitored users."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        client_id: str,
        email: str | None = None,
        display_name: str | None = None,
        telegram_id: int | None = None,
        is_active: bool = True,
    ) -> User:
        user = User(
            client_id=client_id,
            email=email,
            display_name=display_name,
            telegram_id=telegram_id,
            is_active=is_active,
        )
        self._session.add(user)
        await self._session.flush()
        return user

    async def get(self, user_id: int) -> User | None:
        return await self._session.get(User, user_id)

    async def get_by_client_id(self, client_id: str) -> User | None:
        result = await self._session.execute(select(User).where(User.client_id == client_id))
        return result.scalar_one_or_none()

    async def list_active(self, *, limit: int = 100, offset: int = 0) -> Sequence[User]:
        result = await self._session.execute(
            select(User)
            .where(User.is_active.is_(True))
            .order_by(User.client_id)
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().all()

    async def set_active(self, user: User, *, is_active: bool) -> User:
        user.is_active = is_active
        await self._session.flush()
        return user

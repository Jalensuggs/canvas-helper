from typing import Annotated

from fastapi import Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from .db import session_dependency
from .models import LOCAL_USER_ID, User


async def get_current_user(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(session_dependency),
) -> User:
    """Resolve the local desktop owner or an authenticated server session."""
    if request.app.state.settings.deployment_mode == "server":
        user, _ = await request.app.state.auth.authenticate(
            request, response, session
        )
        return user
    user = await session.get(User, LOCAL_USER_ID)
    if user is None:
        user = User(id=LOCAL_USER_ID, display_name="Local User")
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]

import uuid
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.llm import LLMClient, get_llm_client
from app.core.security import decode_access_token
from app.models.user import User

# auto_error=False lets us return 401 ourselves; by default a missing header
# would not carry the WWW-Authenticate header.
bearer_scheme = HTTPBearer(auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ],
    db: DbSession,
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing authentication token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized

    try:
        user_id = uuid.UUID(decode_access_token(credentials.credentials))
    except (jwt.InvalidTokenError, ValueError):
        raise unauthorized from None

    user = await db.get(User, user_id)
    if user is None:
        raise unauthorized
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]

LLMClientDep = Annotated[LLMClient, Depends(get_llm_client)]

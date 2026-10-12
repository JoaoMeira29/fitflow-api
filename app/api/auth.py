import math
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.core.security import create_access_token, hash_password, verify_password
from app.models.login_attempt import LoginAttempt
from app.models.user import User
from app.schemas.auth import LoginRequest, Token
from app.schemas.user import UserCreate, UserRead

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(payload: UserCreate, db: DbSession):
    # Argon2 is deliberately slow, so it runs in a thread to avoid blocking
    # the event loop for other requests.
    password_hash = await run_in_threadpool(hash_password, payload.password)
    user = User(email=payload.email, password_hash=password_hash)
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        # The unique index on email is the source of truth, which also covers
        # two simultaneous sign-ups with the same email.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        ) from None
    await db.refresh(user)
    return user


@router.post("/login", response_model=Token)
async def login(payload: LoginRequest, db: DbSession):
    now = datetime.now(UTC)
    window_start = now - timedelta(minutes=settings.LOGIN_ATTEMPT_WINDOW_MINUTES)

    # Rate limit: the recent wrong passwords for this email, oldest first.
    # Unknown emails are counted too, so the limit behaves the same whether
    # or not an account exists.
    recent_failures = (
        await db.scalars(
            select(LoginAttempt.attempted_at)
            .where(
                LoginAttempt.email == payload.email,
                LoginAttempt.attempted_at > window_start,
            )
            .order_by(LoginAttempt.attempted_at)
        )
    ).all()
    if len(recent_failures) >= settings.LOGIN_MAX_ATTEMPTS:
        # Refused before the password is even checked, right or wrong. One
        # more attempt is allowed once the oldest failure leaves the window.
        oldest_counted = recent_failures[-settings.LOGIN_MAX_ATTEMPTS]
        retry_after = oldest_counted - window_start
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Try again later.",
            headers={"Retry-After": str(math.ceil(retry_after.total_seconds()))},
        )

    user = await db.scalar(select(User).where(User.email == payload.email))
    # Same error for an unknown email and a wrong password, so the endpoint
    # does not reveal which emails are registered.
    if user is None or not await run_in_threadpool(
        verify_password, payload.password, user.password_hash
    ):
        db.add(LoginAttempt(email=payload.email, attempted_at=now))
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # A successful login clears the count. This also removes old rows, so
    # the table does not grow for accounts that log in normally.
    await db.execute(delete(LoginAttempt).where(LoginAttempt.email == payload.email))
    await db.commit()
    return Token(access_token=create_access_token(str(user.id)))


@router.get("/me", response_model=UserRead)
async def read_me(current_user: CurrentUser):
    return current_user

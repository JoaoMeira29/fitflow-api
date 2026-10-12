from fastapi import APIRouter, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, DbSession
from app.core.security import create_access_token, hash_password, verify_password
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
    user = await db.scalar(select(User).where(User.email == payload.email))
    # Same error for an unknown email and a wrong password, so the endpoint
    # does not reveal which emails are registered.
    if user is None or not await run_in_threadpool(
        verify_password, payload.password, user.password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Token(access_token=create_access_token(str(user.id)))


@router.get("/me", response_model=UserRead)
async def read_me(current_user: CurrentUser):
    return current_user

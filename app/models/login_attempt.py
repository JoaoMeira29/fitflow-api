import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LoginAttempt(Base):
    """One failed login. Counting the recent ones for an email is how the
    login endpoint limits password guessing."""

    __tablename__ = "login_attempts"
    # Every lookup is "this email, since this time".
    __table_args__ = (Index("ix_login_attempts_email_time", "email", "attempted_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # The email as typed (lower-cased), whether or not an account exists, so
    # the limit does not reveal which emails are registered.
    email: Mapped[str] = mapped_column(String(320))
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

import uuid

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Exercise(Base):
    __tablename__ = "exercises"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # The name is unique so the seed script can be re-run: it updates the row
    # with the same name and keeps its ID.
    name: Mapped[str] = mapped_column(String(100), unique=True)
    muscle_group: Mapped[str] = mapped_column(String(32), index=True)
    # Every item the exercise needs. A user can do it only if they have all
    # of them.
    equipment: Mapped[list[str]] = mapped_column(ARRAY(String(32)))
    description: Mapped[str] = mapped_column(Text)

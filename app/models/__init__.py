# Import every model here so Base.metadata knows about all tables.
# Alembic and the test suite rely on this.
from app.models.exercise import Exercise
from app.models.food import Food
from app.models.profile import Profile
from app.models.user import User

__all__ = ["Exercise", "Food", "Profile", "User"]

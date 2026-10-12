# Import every model here so Base.metadata knows about all tables.
# Alembic and the test suite rely on this.
from app.models.diet_plan import DietPlan, DietPlanMeal, DietPlanMealItem
from app.models.exercise import Exercise
from app.models.food import Food
from app.models.profile import Profile
from app.models.progress import WeightLog, WorkoutLog
from app.models.user import User
from app.models.workout_plan import WorkoutPlan, WorkoutPlanDay, WorkoutPlanExercise

__all__ = [
    "DietPlan",
    "DietPlanMeal",
    "DietPlanMealItem",
    "Exercise",
    "Food",
    "Profile",
    "User",
    "WeightLog",
    "WorkoutLog",
    "WorkoutPlan",
    "WorkoutPlanDay",
    "WorkoutPlanExercise",
]

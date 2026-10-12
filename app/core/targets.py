"""Daily calorie and macro targets, as pure functions.

Nothing here touches the database, the settings or the LLM, so every function
can be unit tested with plain numbers.
"""

import math
from dataclasses import dataclass

from app.core.enums import ActivityLevel, Goal, Sex

# Total daily energy expenditure = BMR x activity multiplier.
ACTIVITY_MULTIPLIERS = {
    ActivityLevel.SEDENTARY: 1.2,
    ActivityLevel.LIGHT: 1.375,
    ActivityLevel.MODERATE: 1.55,
    ActivityLevel.ACTIVE: 1.725,
    ActivityLevel.VERY_ACTIVE: 1.9,
}

# Share of maintenance calories added or removed for each goal.
GOAL_ADJUSTMENTS = {
    Goal.LOSE_WEIGHT: -0.20,
    Goal.MAINTAIN: 0.0,
    Goal.GAIN_MUSCLE: 0.10,
}

PROTEIN_G_PER_KG = {
    Goal.LOSE_WEIGHT: 2.0,
    Goal.MAINTAIN: 1.6,
    Goal.GAIN_MUSCLE: 2.0,
}
FAT_SHARE_OF_CALORIES = 0.25
# Protein never takes more than this share of the day's calories.
MAX_PROTEIN_SHARE_OF_CALORIES = 0.35

KCAL_PER_G_PROTEIN = 4
KCAL_PER_G_CARBS = 4
KCAL_PER_G_FAT = 9
# Approximate energy in one kilogram of body weight.
KCAL_PER_KG_BODY_WEIGHT = 7700


@dataclass(frozen=True)
class SafetyLimits:
    min_calories_male: int
    min_calories_female: int
    max_weekly_weight_change_percent: float


@dataclass(frozen=True)
class Targets:
    bmr: int
    tdee: int
    calories: int
    protein_g: int
    carbs_g: int
    fat_g: int
    calorie_floor_applied: bool
    weight_change_cap_applied: bool


def calculate_bmr(sex: Sex, weight_kg: float, height_cm: float, age: int) -> float:
    """Basal metabolic rate in kcal per day (Mifflin-St Jeor equation)."""
    base = 10 * weight_kg + 6.25 * height_cm - 5 * age
    return base + 5 if sex == Sex.MALE else base - 161


def calculate_tdee(bmr: float, activity_level: ActivityLevel) -> float:
    """Total daily energy expenditure: the calories that maintain weight."""
    return bmr * ACTIVITY_MULTIPLIERS[activity_level]


def max_daily_calorie_change(weight_kg: float, limits: SafetyLimits) -> float:
    """Largest daily deficit or surplus that keeps weekly weight change within
    the configured percentage of body weight."""
    max_weekly_change_kg = weight_kg * limits.max_weekly_weight_change_percent / 100
    return max_weekly_change_kg * KCAL_PER_KG_BODY_WEIGHT / 7


def calculate_target_calories(
    tdee: float, goal: Goal, sex: Sex, weight_kg: float, limits: SafetyLimits
) -> tuple[int, bool, bool]:
    """Return (calories, calorie_floor_applied, weight_change_cap_applied)."""
    change = tdee * GOAL_ADJUSTMENTS[goal]

    max_change = max_daily_calorie_change(weight_kg, limits)
    cap_applied = abs(change) > max_change
    if cap_applied:
        change = max_change if change > 0 else -max_change

    calories = tdee + change

    floor = (
        limits.min_calories_male if sex == Sex.MALE else limits.min_calories_female
    )
    floor_applied = calories < floor
    if floor_applied:
        calories = floor

    return round(calories), floor_applied, cap_applied


class CalorieTargetError(ValueError):
    """A calorie target the user asked for is outside the safety limits."""


def allowed_calorie_range(
    tdee: float, sex: Sex, weight_kg: float, limits: SafetyLimits
) -> tuple[int, int]:
    """The lowest and highest calorie target a user may choose: never below
    the calorie floor, and never further from maintenance than the weekly
    weight-change cap allows."""
    max_change = max_daily_calorie_change(weight_kg, limits)
    floor = (
        limits.min_calories_male if sex == Sex.MALE else limits.min_calories_female
    )
    lowest = max(floor, math.ceil(tdee - max_change))
    highest = max(lowest, math.floor(tdee + max_change))
    return lowest, highest


def calculate_macros(calories: int, weight_kg: float, goal: Goal) -> tuple[int, int, int]:
    """Return (protein_g, carbs_g, fat_g). Protein follows body weight, up to
    a cap; fat is a fixed share of calories; carbs take the calories left."""
    fat_g = round(calories * FAT_SHARE_OF_CALORIES / KCAL_PER_G_FAT)

    # Protein by body weight asks too much of a heavy person on few calories
    # (the evals found targets of up to 48% of calories), so it is capped at
    # a share of the day's calories.
    protein_cap_g = calories * MAX_PROTEIN_SHARE_OF_CALORIES / KCAL_PER_G_PROTEIN
    protein_g = round(min(PROTEIN_G_PER_KG[goal] * weight_kg, protein_cap_g))

    calories_left = (
        calories - fat_g * KCAL_PER_G_FAT - protein_g * KCAL_PER_G_PROTEIN
    )
    carbs_g = round(calories_left / KCAL_PER_G_CARBS)
    return protein_g, carbs_g, fat_g


def calculate_targets(
    *,
    sex: Sex,
    age: int,
    weight_kg: float,
    height_cm: float,
    activity_level: ActivityLevel,
    goal: Goal,
    limits: SafetyLimits,
    requested_calories: int | None = None,
) -> Targets:
    """Compute the targets for a profile. With `requested_calories`, that
    number replaces the goal-based calorie target, as long as it is inside
    the safety limits; otherwise CalorieTargetError is raised."""
    bmr = calculate_bmr(sex, weight_kg, height_cm, age)
    tdee = calculate_tdee(bmr, activity_level)
    if requested_calories is None:
        calories, floor_applied, cap_applied = calculate_target_calories(
            tdee, goal, sex, weight_kg, limits
        )
    else:
        lowest, highest = allowed_calorie_range(tdee, sex, weight_kg, limits)
        if not lowest <= requested_calories <= highest:
            raise CalorieTargetError(
                f"For your profile, the calorie target must be between "
                f"{lowest} and {highest} kcal"
            )
        calories, floor_applied, cap_applied = requested_calories, False, False
    protein_g, carbs_g, fat_g = calculate_macros(calories, weight_kg, goal)
    return Targets(
        bmr=round(bmr),
        tdee=round(tdee),
        calories=calories,
        protein_g=protein_g,
        carbs_g=carbs_g,
        fat_g=fat_g,
        calorie_floor_applied=floor_applied,
        weight_change_cap_applied=cap_applied,
    )

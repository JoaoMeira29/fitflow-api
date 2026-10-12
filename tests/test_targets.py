import pytest

from app.core.enums import ActivityLevel, Goal, Sex
from app.core.targets import (
    KCAL_PER_G_CARBS,
    KCAL_PER_G_FAT,
    KCAL_PER_G_PROTEIN,
    CalorieTargetError,
    SafetyLimits,
    allowed_calorie_range,
    calculate_bmr,
    calculate_macros,
    calculate_target_calories,
    calculate_targets,
    calculate_tdee,
    max_daily_calorie_change,
)

LIMITS = SafetyLimits(
    min_calories_male=1500,
    min_calories_female=1200,
    max_weekly_weight_change_percent=1.0,
)

# --- BMR and TDEE ----------------------------------------------------------


def test_bmr_for_a_man():
    # 10 * 82.5 + 6.25 * 170 - 5 * 28 + 5
    assert calculate_bmr(Sex.MALE, 82.5, 170, 28) == 1752.5


def test_bmr_for_a_woman():
    # 10 * 60 + 6.25 * 165 - 5 * 30 - 161
    assert calculate_bmr(Sex.FEMALE, 60, 165, 30) == 1320.25


def test_bmr_differs_by_166_kcal_between_sexes():
    male = calculate_bmr(Sex.MALE, 70, 175, 40)
    female = calculate_bmr(Sex.FEMALE, 70, 175, 40)

    assert male - female == 166


@pytest.mark.parametrize(
    "activity_level, expected",
    [
        (ActivityLevel.SEDENTARY, 2400),
        (ActivityLevel.LIGHT, 2750),
        (ActivityLevel.MODERATE, 3100),
        (ActivityLevel.ACTIVE, 3450),
        (ActivityLevel.VERY_ACTIVE, 3800),
    ],
)
def test_tdee_applies_the_activity_multiplier(activity_level, expected):
    assert calculate_tdee(2000, activity_level) == pytest.approx(expected)


# --- Target calories -------------------------------------------------------


def test_losing_weight_removes_20_percent():
    calories, floor, cap = calculate_target_calories(
        2500, Goal.LOSE_WEIGHT, Sex.MALE, 80, LIMITS
    )

    assert (calories, floor, cap) == (2000, False, False)


def test_maintaining_keeps_the_tdee():
    calories, floor, cap = calculate_target_calories(
        2500, Goal.MAINTAIN, Sex.MALE, 80, LIMITS
    )

    assert (calories, floor, cap) == (2500, False, False)


def test_gaining_muscle_adds_10_percent():
    calories, floor, cap = calculate_target_calories(
        2500, Goal.GAIN_MUSCLE, Sex.MALE, 80, LIMITS
    )

    assert (calories, floor, cap) == (2750, False, False)


def test_calorie_floor_for_women():
    calories, floor, _ = calculate_target_calories(
        1300, Goal.LOSE_WEIGHT, Sex.FEMALE, 50, LIMITS
    )

    assert calories == 1200
    assert floor is True


def test_calorie_floor_for_men():
    calories, floor, _ = calculate_target_calories(
        1700, Goal.LOSE_WEIGHT, Sex.MALE, 60, LIMITS
    )

    assert calories == 1500
    assert floor is True


def test_calorie_floor_is_configurable():
    limits = SafetyLimits(
        min_calories_male=1800,
        min_calories_female=1200,
        max_weekly_weight_change_percent=1.0,
    )

    calories, floor, _ = calculate_target_calories(
        2000, Goal.LOSE_WEIGHT, Sex.MALE, 80, limits
    )

    assert calories == 1800
    assert floor is True


def test_max_daily_calorie_change():
    # 1% of 70 kg is 0.7 kg a week: 0.7 * 7700 kcal / 7 days.
    assert max_daily_calorie_change(70, LIMITS) == pytest.approx(770)


def test_weight_loss_is_capped_by_the_weekly_rate():
    limits = SafetyLimits(
        min_calories_male=1500,
        min_calories_female=1200,
        max_weekly_weight_change_percent=0.5,
    )

    # 20% of 3000 is 600, but 0.5% of 80 kg a week allows only 440 a day.
    calories, floor, cap = calculate_target_calories(
        3000, Goal.LOSE_WEIGHT, Sex.MALE, 80, limits
    )

    assert calories == 2560
    assert cap is True
    assert floor is False


def test_weight_gain_is_capped_by_the_weekly_rate():
    limits = SafetyLimits(
        min_calories_male=1500,
        min_calories_female=1200,
        max_weekly_weight_change_percent=0.1,
    )

    # 10% of 3000 is 300, but 0.1% of 80 kg a week allows only 88 a day.
    calories, _, cap = calculate_target_calories(
        3000, Goal.GAIN_MUSCLE, Sex.MALE, 80, limits
    )

    assert calories == 3088
    assert cap is True


# --- A calorie target chosen by the user -----------------------------------


def test_allowed_range_is_maintenance_plus_or_minus_the_weekly_cap():
    # 1% of 80 kg a week allows 880 kcal a day either way.
    assert allowed_calorie_range(2500, Sex.MALE, 80, LIMITS) == (1620, 3380)


def test_allowed_range_never_goes_below_the_calorie_floor():
    # 2000 - 880 would be 1120, below the 1500 kcal floor for men.
    assert allowed_calorie_range(2000, Sex.MALE, 80, LIMITS) == (1500, 2880)


def test_allowed_range_uses_the_floor_for_women():
    assert allowed_calorie_range(1500, Sex.FEMALE, 60, LIMITS) == (1200, 2160)


PROFILE = dict(
    sex=Sex.MALE,
    age=28,
    weight_kg=82.5,
    height_cm=170,
    activity_level=ActivityLevel.SEDENTARY,
    goal=Goal.LOSE_WEIGHT,
    limits=LIMITS,
)


def test_requested_calories_replace_the_goal_based_target():
    targets = calculate_targets(**PROFILE, requested_calories=1700)

    assert targets.calories == 1700
    assert targets.tdee == 2103  # maintenance is still reported
    assert targets.protein_g == 165  # protein still follows body weight
    assert targets.fat_g == 47  # 25% of 1700 kcal / 9
    assert targets.carbs_g == 154
    assert targets.calorie_floor_applied is False
    assert targets.weight_change_cap_applied is False


def test_requested_calories_below_the_floor_are_rejected():
    with pytest.raises(CalorieTargetError, match="between 1500 and 3010 kcal"):
        calculate_targets(**PROFILE, requested_calories=1499)


def test_requested_calories_beyond_the_weekly_cap_are_rejected():
    # Moderately active, maintenance is 2716 kcal; 1% a week allows 907 less.
    profile = {**PROFILE, "activity_level": ActivityLevel.MODERATE}

    with pytest.raises(CalorieTargetError, match="between 1809 and 3623 kcal"):
        calculate_targets(**profile, requested_calories=1700)


def test_requested_calories_at_the_edges_are_accepted():
    assert calculate_targets(**PROFILE, requested_calories=1500).calories == 1500
    assert calculate_targets(**PROFILE, requested_calories=3010).calories == 3010


# --- Macros ----------------------------------------------------------------


def test_macros_when_losing_weight():
    protein_g, carbs_g, fat_g = calculate_macros(2000, 80, Goal.LOSE_WEIGHT)

    assert protein_g == 160  # 2.0 g per kg
    assert fat_g == 56  # 25% of 2000 kcal / 9
    assert carbs_g == 214  # (2000 - 160 * 4 - 56 * 9) / 4


def test_macros_use_less_protein_when_maintaining():
    protein_g, _, _ = calculate_macros(2500, 80, Goal.MAINTAIN)

    assert protein_g == 128  # 1.6 g per kg


@pytest.mark.parametrize("calories", [1200, 1873, 2500, 3999])
@pytest.mark.parametrize("weight_kg", [45, 82.5, 140])
@pytest.mark.parametrize("goal", list(Goal))
def test_macros_add_up_to_the_calorie_target(calories, weight_kg, goal):
    protein_g, carbs_g, fat_g = calculate_macros(calories, weight_kg, goal)

    total = (
        protein_g * KCAL_PER_G_PROTEIN
        + carbs_g * KCAL_PER_G_CARBS
        + fat_g * KCAL_PER_G_FAT
    )
    # Rounding each macro to whole grams can move the total by a few kcal.
    assert abs(total - calories) <= 4


def test_protein_is_limited_to_the_calories_left_after_fat():
    # 2.0 g per kg would be 600 g (2400 kcal), more than the whole target.
    protein_g, carbs_g, fat_g = calculate_macros(1200, 300, Goal.LOSE_WEIGHT)

    assert fat_g == 33
    assert protein_g == 226  # the 903 kcal left after fat, not 600 g
    assert carbs_g == 0


# --- calculate_targets -----------------------------------------------------


def test_targets_for_a_full_profile():
    targets = calculate_targets(
        sex=Sex.MALE,
        age=28,
        weight_kg=82.5,
        height_cm=170,
        activity_level=ActivityLevel.MODERATE,
        goal=Goal.LOSE_WEIGHT,
        limits=LIMITS,
    )

    assert targets.bmr == 1752  # 1752.5 rounds to the even number
    assert targets.tdee == 2716  # 1752.5 * 1.55
    assert targets.calories == 2173  # 2716.375 * 0.8
    assert targets.protein_g == 165
    assert targets.fat_g == 60
    assert targets.carbs_g == 243
    assert targets.calorie_floor_applied is False
    assert targets.weight_change_cap_applied is False


def test_targets_report_the_calorie_floor():
    targets = calculate_targets(
        sex=Sex.FEMALE,
        age=60,
        weight_kg=45,
        height_cm=150,
        activity_level=ActivityLevel.SEDENTARY,
        goal=Goal.LOSE_WEIGHT,
        limits=LIMITS,
    )

    assert targets.calories == 1200
    assert targets.calorie_floor_applied is True

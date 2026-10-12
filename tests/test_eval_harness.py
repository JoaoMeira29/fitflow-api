"""Regular tests for the eval harness, with the LLM mocked. They keep the
harness working even though the evals themselves only run on demand."""

import json
from datetime import date

from app.api.profile import targets_for_profile
from app.core.age import MIN_AGE, calculate_age
from tests.conftest import FakeLLM
from tests.evals.harness import (
    eval_diet,
    eval_workout,
    load_exercises,
    load_foods,
    load_profiles,
    render_report,
    success_rate,
)

# --- Profiles --------------------------------------------------------------


def test_there_are_at_least_30_profiles_with_unique_names():
    profiles = load_profiles()

    names = [case.name for case in profiles]
    assert len(profiles) >= 30
    assert len(names) == len(set(names))


def test_every_profile_is_an_adult():
    for case in load_profiles():
        assert calculate_age(case.profile.birth_date) >= MIN_AGE, case.name


def test_profiles_cover_every_goal_sex_and_diet():
    profiles = [case.profile for case in load_profiles()]

    assert {p.goal for p in profiles} == {"lose_weight", "maintain", "gain_muscle"}
    assert {p.sex for p in profiles} == {"male", "female"}
    diets = {diet for p in profiles for diet in p.dietary_restrictions}
    assert diets == {"vegetarian", "vegan", "pescatarian"}
    assert {p.training_days_per_week for p in profiles} >= {1, 7}


def test_limit_returns_the_first_profiles():
    assert len(load_profiles(limit=3)) == 3


# --- eval_workout ----------------------------------------------------------


def workout_reply(exercises, days: int, names=("Push-up", "Plank", "Crunch")) -> str:
    by_name = {exercise.name: exercise for exercise in exercises}
    day = {
        "focus": "Full body",
        "exercises": [
            {
                "exercise_id": str(by_name[name].id),
                "sets": 3,
                "reps": 10,
                "rest_seconds": 60,
            }
            for name in names
        ],
    }
    return json.dumps({"days": [day] * days})


def home_bodyweight():
    """A profile with 3 training days and only bodyweight equipment."""
    return next(case for case in load_profiles() if case.name == "home_bodyweight")


async def test_eval_workout_records_a_valid_first_answer():
    exercises = load_exercises()
    llm = FakeLLM()
    llm.replies = [workout_reply(exercises, days=3)]

    result = await eval_workout(llm, home_bodyweight(), exercises)

    assert result.valid is True
    assert result.first_attempt_valid is True
    assert result.attempts == 1
    assert all(result.first_answer.values())


async def test_eval_workout_records_which_rules_the_first_answer_broke():
    exercises = load_exercises()
    llm = FakeLLM()
    llm.replies = [
        # Two days instead of three, and a barbell exercise.
        workout_reply(exercises, days=2, names=("Push-up", "Plank", "Deadlift")),
        workout_reply(exercises, days=3),
    ]

    result = await eval_workout(llm, home_bodyweight(), exercises)

    assert result.valid is True
    assert result.first_attempt_valid is False
    assert result.attempts == 2
    assert result.first_answer == {
        "schema": True,
        "day_count": False,
        "known_exercise": True,
        "equipment": False,
        "no_repeats": True,
    }


async def test_eval_workout_cannot_judge_rules_when_the_reply_is_not_json():
    exercises = load_exercises()
    llm = FakeLLM()
    llm.replies = ["not JSON"] * 3

    result = await eval_workout(llm, home_bodyweight(), exercises)

    assert result.valid is False
    assert result.attempts == 3
    assert result.first_answer["schema"] is False
    assert result.first_answer["day_count"] is None
    assert result.errors


# --- eval_diet -------------------------------------------------------------


def diet_reply(foods, **meals: dict[str, float]) -> str:
    by_name = {food.name: food for food in foods}
    return json.dumps(
        {
            "meals": [
                {
                    "meal_type": meal_type,
                    "items": [
                        {"food_id": str(by_name[name].id), "grams": grams}
                        for name, grams in items.items()
                    ],
                }
                for meal_type, items in meals.items()
            ]
        }
    )


def carb_heavy_reply(foods) -> str:
    """Sensible foods but far too little protein, and too few calories."""
    return diet_reply(
        foods,
        breakfast={"Egg, whole": 50, "Oats, rolled": 60},
        lunch={"Chicken breast, raw": 50, "White rice, cooked": 250},
        dinner={"Salmon, raw": 40, "Pasta, cooked": 250},
        snack={"Greek yogurt, plain, 0% fat": 50, "Banana": 150},
    )


async def test_eval_diet_shows_what_each_code_step_fixes():
    foods = load_foods()
    llm = FakeLLM()
    llm.replies = [carb_heavy_reply(foods)]

    result = await eval_diet(llm, home_bodyweight(), foods)

    # The pipeline succeeds on the first attempt, thanks to the code.
    assert result.valid is True
    assert result.attempts == 1
    first = result.first_answer
    assert first["schema"] is True
    assert first["allergens"] is True
    # As written by the model, both numbers miss.
    assert first["raw_calories"] is False
    assert first["raw_protein"] is False
    # Scaling fixes the calories but not the protein.
    assert first["scaled_calories"] is True
    assert first["scaled_protein"] is False
    # The protein repair fixes the rest.
    assert first["repaired_calories"] is True
    assert first["repaired_protein"] is True
    assert first["repaired_portions"] is True


async def test_eval_diet_does_no_arithmetic_on_a_plan_with_bad_foods():
    foods = load_foods()
    vegan = next(case for case in load_profiles() if case.name == "vegan_cut")
    llm = FakeLLM()
    llm.replies = [carb_heavy_reply(foods)] * 3  # eggs, chicken, salmon, yogurt

    result = await eval_diet(llm, vegan, foods)

    assert result.valid is False
    assert result.first_answer["restrictions"] is False
    assert result.first_answer["raw_calories"] is None
    assert result.first_answer["repaired_protein"] is None


def test_eval_profiles_get_targets_from_the_app_code():
    case = home_bodyweight()

    targets = targets_for_profile(case.profile)

    assert targets.calories > 1200
    assert targets.protein_g == round(1.6 * case.profile.weight_kg)


# --- Report ----------------------------------------------------------------


async def test_report_shows_pass_rates_and_failures():
    exercises, foods = load_exercises(), load_foods()
    case = home_bodyweight()
    llm = FakeLLM()
    llm.replies = [
        workout_reply(exercises, days=3),
        workout_reply(exercises, days=2),
        workout_reply(exercises, days=2),
        workout_reply(exercises, days=2),
        carb_heavy_reply(foods),
    ]
    workout = [
        await eval_workout(llm, case, exercises),
        await eval_workout(llm, case, exercises),
    ]
    diet = [await eval_diet(llm, case, foods)]

    report = render_report(
        model="test-model", run_date=date(2026, 1, 2), workout=workout, diet=diet
    )

    assert success_rate(workout) == 0.5
    assert "- Model: `test-model`" in report
    assert "- Date: 2026-01-02" in report
    assert "## Workout plans (2 profiles)" in report
    assert "| Right number of training days | 1/2 | 50% |" in report
    assert "| **Valid plan produced** | 1/2 | 50% |" in report
    assert "- `home_bodyweight`: The plan has 2 days but must have exactly 3." in report
    assert "## Diet plans (1 profiles)" in report
    assert "| Protein at or above target | 0/1 | 0% |" in report
    assert "| Protein at or above target | 1/1 | 100% |" in report

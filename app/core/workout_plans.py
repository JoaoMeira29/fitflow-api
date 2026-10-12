"""Generate a workout plan with the LLM and validate it in code.

The LLM only chooses: which exercises, in what order, with how many sets and
reps. Everything it returns is checked here against the catalog and the
user's profile. The retry loop itself lives in app/core/plan_generation.py.
"""

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.core.enums import Equipment
from app.core.llm import LLMClient, Message
from app.core.plan_generation import generate_valid_plan
from app.models.exercise import Exercise
from app.schemas.profile import ProfileRead
from app.schemas.workout import WorkoutPlanDraft

SYSTEM_PROMPT = """\
You are an experienced strength and conditioning coach. You build weekly \
workout plans.

Rules:
- Use only exercises from the list you are given, referenced by their id.
- Create exactly the number of training days you are asked for.
- Each day has 4 to 6 exercises, and no exercise appears twice in one day.
- Give each day a short focus, for example "Upper body" or "Legs and core".
- Spread the muscle groups sensibly across the week.
- Choose sets, reps and rest that suit the person's goal and activity level.
- Reply with JSON only."""


@dataclass(frozen=True)
class GeneratedWorkoutPlan:
    draft: WorkoutPlanDraft
    model: str
    attempts: int


def available_equipment(profile_equipment: Iterable[str]) -> set[str]:
    """What the user can train with. Everyone has their own body weight."""
    return {*profile_equipment, Equipment.BODYWEIGHT.value}


def doable_exercises(
    exercises: Iterable[Exercise], equipment: set[str]
) -> list[Exercise]:
    """The exercises that need nothing the user does not have."""
    return [exercise for exercise in exercises if set(exercise.equipment) <= equipment]


def validate_workout_plan(
    draft: WorkoutPlanDraft,
    *,
    training_days: int,
    exercises_by_id: Mapping[uuid.UUID, Exercise],
    equipment: set[str],
) -> list[str]:
    """Return every problem found in the plan. An empty list means valid."""
    errors = []

    if len(draft.days) != training_days:
        errors.append(
            f"The plan has {len(draft.days)} days but must have exactly "
            f"{training_days}."
        )

    for day_number, day in enumerate(draft.days, start=1):
        seen = set()
        for item in day.exercises:
            exercise = exercises_by_id.get(item.exercise_id)
            if exercise is None:
                errors.append(
                    f"Day {day_number}: exercise id {item.exercise_id} is not "
                    "in the list of available exercises."
                )
                continue

            missing = set(exercise.equipment) - equipment
            if missing:
                errors.append(
                    f"Day {day_number}: '{exercise.name}' needs equipment the "
                    f"user does not have ({', '.join(sorted(missing))})."
                )

            if exercise.id in seen:
                errors.append(
                    f"Day {day_number}: '{exercise.name}' appears more than once."
                )
            seen.add(exercise.id)

    return errors


def plan_json_schema(training_days: int, allowed_ids: Iterable[uuid.UUID]) -> dict:
    """The JSON schema sent to the LLM. It is the Pydantic schema, narrowed to
    this request: the exact number of days and only the exercise ids on
    offer. A provider that enforces schemas then cannot return anything else,
    and validate_workout_plan still checks the result in case it does."""
    schema = WorkoutPlanDraft.model_json_schema()
    schema["properties"]["days"]["minItems"] = training_days
    schema["properties"]["days"]["maxItems"] = training_days
    schema["$defs"]["WorkoutExerciseDraft"]["properties"]["exercise_id"] = {
        "type": "string",
        "enum": [str(exercise_id) for exercise_id in allowed_ids],
    }
    return schema


def build_messages(
    profile: ProfileRead, age: int, exercises: Iterable[Exercise]
) -> list[Message]:
    exercise_lines = "\n".join(
        f"{exercise.id} | {exercise.name} | {exercise.muscle_group}"
        for exercise in exercises
    )
    user_prompt = f"""\
Build a workout plan for this person.

Goal: {profile.goal.value}
Age: {age}
Sex: {profile.sex.value}
Activity level: {profile.activity_level.value}
Training days per week: {profile.training_days_per_week}

Available exercises (id | name | muscle group):
{exercise_lines}"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


async def generate_workout_plan(
    llm: LLMClient,
    *,
    profile: ProfileRead,
    age: int,
    exercises: Iterable[Exercise],
) -> GeneratedWorkoutPlan:
    """Ask the LLM for a plan until it passes validation. Raises
    PlanGenerationError when it never does, and lets LLMError through when
    the model cannot be reached."""
    exercises_by_id = {exercise.id: exercise for exercise in exercises}
    equipment = available_equipment(profile.equipment)
    # The model is only shown exercises the user can actually do.
    allowed = doable_exercises(exercises_by_id.values(), equipment)

    def check(draft: WorkoutPlanDraft) -> tuple[WorkoutPlanDraft, list[str]]:
        errors = validate_workout_plan(
            draft,
            training_days=profile.training_days_per_week,
            exercises_by_id=exercises_by_id,
            equipment=equipment,
        )
        return draft, errors

    draft, model, attempts = await generate_valid_plan(
        llm,
        messages=build_messages(profile, age, allowed),
        json_schema=plan_json_schema(
            profile.training_days_per_week, [exercise.id for exercise in allowed]
        ),
        draft_model=WorkoutPlanDraft,
        check=check,
    )
    return GeneratedWorkoutPlan(draft=draft, model=model, attempts=attempts)

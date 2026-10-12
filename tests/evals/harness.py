"""Run the real plan generation for a profile and record what happened.

For every profile the harness records two things: how the model's FIRST
answer did against each validation rule, and whether the whole pipeline
(code repairs plus retries) ended with a valid plan. It calls the same
functions the API uses, so it measures the real behaviour.
"""

import json
import statistics
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from app.api.profile import targets_for_profile
from app.core import diet_plans, workout_plans
from app.core.plan_generation import (
    SCHEMA_RULE,
    AttemptRecord,
    Issue,
    PlanGenerationError,
)
from app.models.exercise import Exercise
from app.models.food import Food
from app.schemas.diet import DietPlanDraft
from app.schemas.exercise import ExerciseBase
from app.schemas.food import FoodBase
from app.schemas.profile import ProfileRead
from app.seed import load_catalog

PROFILES_FILE = Path(__file__).resolve().parent / "profiles.json"

# Each check: (key, label shown in the report). The order is the report order.
WORKOUT_CHECKS = [
    (SCHEMA_RULE, "Matches the JSON schema"),
    (workout_plans.RULE_DAY_COUNT, "Right number of training days"),
    (workout_plans.RULE_KNOWN_EXERCISE, "Every exercise is in the catalog"),
    (workout_plans.RULE_EQUIPMENT, "Every exercise matches the user's equipment"),
    (workout_plans.RULE_NO_REPEATS, "No exercise repeated in a day"),
]

DIET_FOOD_CHECKS = [
    (SCHEMA_RULE, "Matches the JSON schema"),
    (diet_plans.RULE_MEALS, "Has breakfast, lunch, dinner and snack"),
    (diet_plans.RULE_KNOWN_FOOD, "Every food is in the catalog"),
    (diet_plans.RULE_ALLERGENS, "No allergens"),
    (diet_plans.RULE_RESTRICTIONS, "Respects dietary restrictions"),
    (diet_plans.RULE_NO_REPEATS, "No food repeated in a meal"),
]
DIET_RAW_CHECKS = [
    ("raw_calories", "Calories within 5% of target"),
    ("raw_protein", "Protein at or above target"),
]
DIET_SCALED_CHECKS = [
    ("scaled_calories", "Calories within 5% of target"),
    ("scaled_protein", "Protein at or above target"),
    ("scaled_portions", "Every portion a sensible size"),
]
DIET_REPAIRED_CHECKS = [
    ("repaired_calories", "Calories within 5% of target"),
    ("repaired_protein", "Protein at or above target"),
    ("repaired_portions", "Every portion a sensible size"),
]
DIET_CAPPED_CHECKS = [
    ("capped_calories", "Calories within 5% of target"),
    ("capped_protein", "Protein at or above target"),
    ("capped_portions", "Every portion a sensible size"),
]


@dataclass(frozen=True)
class EvalProfile:
    name: str
    profile: ProfileRead


@dataclass
class EvalResult:
    profile: str
    # Check key -> passed. None means it could not be evaluated, for example
    # the calories of a plan that uses a food that does not exist.
    first_answer: dict[str, bool | None]
    first_attempt_valid: bool
    valid: bool
    attempts: int
    seconds: float
    errors: list[str] = field(default_factory=list)


def load_profiles(limit: int | None = None) -> list[EvalProfile]:
    rows = json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
    this_year = date.today().year
    profiles = []
    for row in rows[:limit]:
        row = dict(row)
        name = row.pop("name")
        # Born on 1 January, so the profile has exactly this age all year.
        birth_date = date(this_year - row.pop("age"), 1, 1)
        profiles.append(
            EvalProfile(name, ProfileRead(birth_date=birth_date, **row))
        )
    return profiles


def load_exercises() -> list[Exercise]:
    """The exercise catalog from its JSON file, so no database is needed. The
    ids are derived from the names, which keeps them stable between runs."""
    return [
        Exercise(id=uuid.uuid5(uuid.NAMESPACE_DNS, row.name), **row.model_dump())
        for row in load_catalog("exercises.json", ExerciseBase)
    ]


def load_foods() -> list[Food]:
    return [
        Food(id=uuid.uuid5(uuid.NAMESPACE_DNS, row.name), **row.model_dump())
        for row in load_catalog("foods.json", FoodBase)
    ]


def _broken_rules(issues: list[Issue]) -> set[str]:
    return {issue.rule for issue in issues}


def _rule_results(
    checks: list[tuple[str, str]], broken: set[str], evaluated: bool = True
) -> dict[str, bool | None]:
    return {key: (key not in broken if evaluated else None) for key, _ in checks}


async def eval_workout(llm, case: EvalProfile, exercises: list[Exercise]) -> EvalResult:
    log: list[AttemptRecord] = []
    started = time.perf_counter()
    valid, errors = True, []
    try:
        await workout_plans.generate_workout_plan(
            llm,
            profile=case.profile,
            age=_age(case.profile),
            exercises=exercises,
            attempt_log=log,
        )
    except PlanGenerationError as error:
        valid, errors = False, error.errors

    first = log[0]
    broken = _broken_rules(first.issues)
    schema_ok = SCHEMA_RULE not in broken
    first_answer = {SCHEMA_RULE: schema_ok}
    # The other rules can only be judged when the reply could be parsed.
    first_answer |= _rule_results(WORKOUT_CHECKS[1:], broken, evaluated=schema_ok)
    return EvalResult(
        profile=case.name,
        first_answer=first_answer,
        first_attempt_valid=not first.issues,
        valid=valid,
        attempts=len(log),
        seconds=time.perf_counter() - started,
        errors=errors,
    )


def _diet_first_answer(
    content: str, profile: ProfileRead, targets, foods_by_id
) -> dict[str, bool | None]:
    """Judge the model's first reply at each stage of the pipeline: as
    written, after calorie scaling, after the protein repair, and after
    oversized portions are capped."""
    numeric_checks = (
        DIET_RAW_CHECKS + DIET_SCALED_CHECKS + DIET_REPAIRED_CHECKS + DIET_CAPPED_CHECKS
    )
    try:
        raw = DietPlanDraft.model_validate_json(content)
    except ValidationError:
        return (
            {SCHEMA_RULE: False}
            | _rule_results(DIET_FOOD_CHECKS[1:], set(), evaluated=False)
            | _rule_results(numeric_checks, set(), evaluated=False)
        )

    food_issues = diet_plans.validate_foods(
        raw,
        foods_by_id=foods_by_id,
        allergies=profile.allergies,
        restrictions=profile.dietary_restrictions,
    )
    results = {SCHEMA_RULE: True} | _rule_results(
        DIET_FOOD_CHECKS[1:], _broken_rules(food_issues)
    )
    if food_issues:
        # Arithmetic on unknown foods or missing meals is meaningless.
        return results | _rule_results(numeric_checks, set(), evaluated=False)

    def broken_targets(plan: DietPlanDraft) -> set[str]:
        return _broken_rules(
            diet_plans.validate_targets(
                plan,
                foods_by_id=foods_by_id,
                target_kcal=targets.calories,
                target_protein_g=targets.protein_g,
            )
        )

    scaled = diet_plans.rescale_to_calories(raw, foods_by_id, targets.calories)
    repaired = diet_plans.rebalance_for_protein(scaled, foods_by_id, targets.protein_g)
    capped = diet_plans.cap_portions(repaired, foods_by_id)
    stages = (
        ("raw", raw),
        ("scaled", scaled),
        ("repaired", repaired),
        ("capped", capped),
    )
    for prefix, plan in stages:
        broken = broken_targets(plan)
        results[f"{prefix}_calories"] = diet_plans.RULE_CALORIES not in broken
        results[f"{prefix}_protein"] = diet_plans.RULE_PROTEIN not in broken
        if prefix != "raw":
            results[f"{prefix}_portions"] = diet_plans.RULE_PORTIONS not in broken
    return results


async def eval_diet(llm, case: EvalProfile, foods: list[Food]) -> EvalResult:
    targets = targets_for_profile(case.profile)
    foods_by_id = {food.id: food for food in foods}
    log: list[AttemptRecord] = []
    started = time.perf_counter()
    valid, errors = True, []
    try:
        await diet_plans.generate_diet_plan(
            llm, profile=case.profile, targets=targets, foods=foods, attempt_log=log
        )
    except PlanGenerationError as error:
        valid, errors = False, error.errors

    first = log[0]
    return EvalResult(
        profile=case.name,
        first_answer=_diet_first_answer(
            first.content, case.profile, targets, foods_by_id
        ),
        first_attempt_valid=not first.issues,
        valid=valid,
        attempts=len(log),
        seconds=time.perf_counter() - started,
        errors=errors,
    )


def _age(profile: ProfileRead) -> int:
    return date.today().year - profile.birth_date.year


def success_rate(results: list[EvalResult]) -> float:
    return sum(result.valid for result in results) / len(results)


# --- Report ----------------------------------------------------------------


def _rate_row(label: str, passed: int, total: int) -> str:
    rate = f"{round(100 * passed / total)}%" if total else "n/a"
    return f"| {label} | {passed}/{total} | {rate} |"


def _check_rows(results: list[EvalResult], checks: list[tuple[str, str]]) -> list[str]:
    rows = []
    for key, label in checks:
        outcomes = [r.first_answer[key] for r in results if r.first_answer[key] is not None]
        rows.append(_rate_row(label, sum(outcomes), len(outcomes)))
    return rows


def _table(title: str, rows: list[str]) -> list[str]:
    return [f"**{title}**", "", "| Check | Passed | Rate |", "| --- | --- | --- |", *rows, ""]


def _outcome_lines(results: list[EvalResult]) -> list[str]:
    total = len(results)
    by_attempts = {
        n: sum(1 for r in results if r.valid and r.attempts == n) for n in (1, 2, 3)
    }
    failed = [r for r in results if not r.valid]
    lines = _table(
        "End result",
        [
            _rate_row("Valid on the first attempt", by_attempts[1], total),
            _rate_row("Valid after 1 retry", by_attempts[2], total),
            _rate_row("Valid after 2 retries", by_attempts[3], total),
            _rate_row("**Valid plan produced**", total - len(failed), total),
            _rate_row("Failed after 2 retries", len(failed), total),
        ],
    )
    seconds = [r.seconds for r in results]
    lines += [
        f"Time per plan: median {statistics.median(seconds):.0f} s, "
        f"slowest {max(seconds):.0f} s.",
        "",
    ]
    if failed:
        lines += ["Profiles that failed:", ""]
        for result in failed:
            lines.append(f"- `{result.profile}`: {' '.join(result.errors)}")
        lines.append("")
    return lines


def render_report(
    *,
    model: str,
    run_date: date,
    workout: list[EvalResult] | None,
    diet: list[EvalResult] | None,
) -> str:
    lines = [
        "# FitFlow eval results",
        "",
        f"- Model: `{model}`",
        f"- Date: {run_date.isoformat()}",
        "",
        "Each profile is run once through the same code the API uses. "
        '"The model\'s first answer" counts every rule separately, before any '
        'retry. "End result" is what the user gets after code repairs and at '
        "most 2 retries.",
        "",
    ]
    if workout:
        lines += [f"## Workout plans ({len(workout)} profiles)", ""]
        lines += _table("The model's first answer", _check_rows(workout, WORKOUT_CHECKS))
        lines += _outcome_lines(workout)
    if diet:
        lines += [f"## Diet plans ({len(diet)} profiles)", ""]
        lines += _table(
            "The model's first answer: food choices",
            _check_rows(diet, DIET_FOOD_CHECKS),
        )
        lines += _table(
            "The model's first answer: numbers, exactly as the model wrote them",
            _check_rows(diet, DIET_RAW_CHECKS),
        )
        lines += _table(
            "The same answer after the code scales each meal to its calories",
            _check_rows(diet, DIET_SCALED_CHECKS),
        )
        lines += _table(
            "The same answer after the code also repairs protein",
            _check_rows(diet, DIET_REPAIRED_CHECKS),
        )
        lines += _table(
            "The same answer after the code also caps oversized portions",
            _check_rows(diet, DIET_CAPPED_CHECKS),
        )
        lines += _outcome_lines(diet)
    return "\n".join(lines).rstrip() + "\n"

"""Generate a one-day meal plan with the LLM and validate it in code.

The LLM only chooses foods and rough portions. The code does all the
arithmetic: it scales each meal to its share of the calorie target, moves
calories towards the high-protein foods if protein is short, and checks the
plan against the catalog, the user's allergies and restrictions, and the
targets. The retry loop itself lives in app/core/plan_generation.py.
"""

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.core.enums import MealType
from app.core.llm import LLMClient, Message, Tool, ToolError
from app.core.plan_generation import AttemptRecord, Issue, generate_valid_plan
from app.core.targets import Targets
from app.models.food import Food
from app.schemas.diet import DietItemDraft, DietMealDraft, DietPlanDraft
from app.schemas.profile import ProfileRead

# The validation rules, by name. The evals report a pass rate for each one.
RULE_MEALS = "meals"
RULE_KNOWN_FOOD = "known_food"
RULE_ALLERGENS = "allergens"
RULE_RESTRICTIONS = "restrictions"
RULE_NO_REPEATS = "no_repeats"
RULE_CALORIES = "calories"
RULE_PROTEIN = "protein"
RULE_PORTIONS = "portions"

NUTRITION_TOOL_NAME = "calculate_nutrition"

# Daily calories may differ from the target by at most this share.
CALORIE_TOLERANCE = 0.05
# Limits for one food in one meal, after scaling.
MIN_PORTION_GRAMS = 5
MAX_PORTION_GRAMS = 600

# A food counts as a protein source with at least this much protein per
# 100 g and at least this share of its calories from protein. Both are needed:
# broccoli passes the second test but nobody eats it for protein.
HIGH_PROTEIN_MIN_G_PER_100G = 8
HIGH_PROTEIN_MIN_CALORIE_SHARE = 0.25
# When protein is short, at most this share of a meal's low-protein calories
# is moved to its high-protein foods.
MAX_PROTEIN_SHIFT = 0.5
# Aim slightly above the target so rounding to whole grams cannot undershoot.
PROTEIN_MARGIN_G = 2

# The share of the day's calories each meal gets when portions are scaled.
MEAL_CALORIE_SHARES = {
    MealType.BREAKFAST: 0.25,
    MealType.LUNCH: 0.35,
    MealType.DINNER: 0.30,
    MealType.SNACK: 0.10,
}
REQUIRED_MEALS = list(MEAL_CALORIE_SHARES)

SYSTEM_PROMPT = """\
You are an experienced dietitian. You build meal plans for one day.

Rules:
- Use only foods from the list you are given, referenced by their id.
- Create exactly four meals: breakfast, lunch, dinner and snack.
- Each meal has 2 to 5 foods, each with a portion in grams, and no food \
appears twice in one meal.
- A portion is of the food as listed: raw, cooked or dry, as its name says.
- Give the portions you would really serve, for example 150 g of meat or \
fish, 200 g of cooked rice or pasta, 150 g of vegetables, 40 g of oats.
- Breakfast is a normal breakfast, for example eggs, oats, yogurt, bread, \
milk or fruit.
- Lunch and dinner each have one main protein food (meat, fish, eggs, tofu \
or legumes), one starchy food (rice, pasta, potato, bread or another grain) \
and one or two vegetables.
- The snack is light, for example fruit, yogurt or nuts.
- Use different foods at lunch and at dinner.
- Every meal includes a high-protein food so the day reaches the protein \
target.
- Your portions will be scaled to the calorie target afterwards, so focus on \
choosing the right foods in the right proportions.
- Reply with JSON only."""


@dataclass(frozen=True)
class Nutrients:
    kcal: float = 0
    protein_g: float = 0
    carbs_g: float = 0
    fat_g: float = 0

    def __add__(self, other: "Nutrients") -> "Nutrients":
        return Nutrients(
            self.kcal + other.kcal,
            self.protein_g + other.protein_g,
            self.carbs_g + other.carbs_g,
            self.fat_g + other.fat_g,
        )


@dataclass(frozen=True)
class GeneratedDietPlan:
    draft: DietPlanDraft
    model: str
    attempts: int


def food_nutrients(food: Food, grams: float) -> Nutrients:
    """Nutrients in `grams` of a food. Catalog values are per 100 g."""
    share = grams / 100
    return Nutrients(
        kcal=food.kcal_per_100g * share,
        protein_g=food.protein_per_100g * share,
        carbs_g=food.carbs_per_100g * share,
        fat_g=food.fat_per_100g * share,
    )


def meal_nutrients(
    items: Iterable[DietItemDraft], foods_by_id: Mapping[uuid.UUID, Food]
) -> Nutrients:
    return sum(
        (food_nutrients(foods_by_id[item.food_id], item.grams) for item in items),
        Nutrients(),
    )


def plan_nutrients(
    draft: DietPlanDraft, foods_by_id: Mapping[uuid.UUID, Food]
) -> Nutrients:
    return sum(
        (meal_nutrients(meal.items, foods_by_id) for meal in draft.meals),
        Nutrients(),
    )


def allowed_foods(
    foods: Iterable[Food], allergies: Iterable[str], restrictions: Iterable[str]
) -> list[Food]:
    """The foods the user can eat: none of their allergens, and suitable for
    every one of their dietary restrictions."""
    allergies, restrictions = set(allergies), set(restrictions)
    return [
        food
        for food in foods
        if not allergies & set(food.allergens) and restrictions <= set(food.diet_tags)
    ]


def validate_foods(
    draft: DietPlanDraft,
    *,
    foods_by_id: Mapping[uuid.UUID, Food],
    allergies: Iterable[str],
    restrictions: Iterable[str],
) -> list[Issue]:
    """Check the structure and the food choices, before any arithmetic."""
    allergies, restrictions = set(allergies), set(restrictions)
    issues = []

    meal_types = [meal.meal_type for meal in draft.meals]
    if sorted(meal_types) != sorted(REQUIRED_MEALS):
        issues.append(
            Issue(
                RULE_MEALS,
                "The plan must have exactly four meals: breakfast, lunch, "
                f"dinner and snack. It has: {', '.join(meal_types)}.",
            )
        )

    for meal in draft.meals:
        seen = set()
        for item in meal.items:
            food = foods_by_id.get(item.food_id)
            if food is None:
                issues.append(
                    Issue(
                        RULE_KNOWN_FOOD,
                        f"{meal.meal_type}: food id {item.food_id} is not in "
                        "the list of available foods.",
                    )
                )
                continue

            found_allergens = allergies & set(food.allergens)
            if found_allergens:
                issues.append(
                    Issue(
                        RULE_ALLERGENS,
                        f"{meal.meal_type}: '{food.name}' contains an allergen "
                        "the user must avoid "
                        f"({', '.join(sorted(found_allergens))}).",
                    )
                )

            unmet = restrictions - set(food.diet_tags)
            if unmet:
                issues.append(
                    Issue(
                        RULE_RESTRICTIONS,
                        f"{meal.meal_type}: '{food.name}' is not suitable for "
                        f"a {', '.join(sorted(unmet))} diet.",
                    )
                )

            if food.id in seen:
                issues.append(
                    Issue(
                        RULE_NO_REPEATS,
                        f"{meal.meal_type}: '{food.name}' appears more than once.",
                    )
                )
            seen.add(food.id)

    return issues


def rescale_to_calories(
    draft: DietPlanDraft, foods_by_id: Mapping[uuid.UUID, Food], target_kcal: int
) -> DietPlanDraft:
    """Scale the portions so the day's calories match the target.

    Each meal is scaled on its own to its share of the day (see
    MEAL_CALORIE_SHARES). Inside a meal every food is multiplied by the same
    factor, so the proportions the model chose are kept. Portions are rounded
    to whole grams. Expects the four required meals."""
    meals = []
    for meal in draft.meals:
        meal_kcal = meal_nutrients(meal.items, foods_by_id).kcal
        meal_target = target_kcal * MEAL_CALORIE_SHARES[meal.meal_type]
        factor = meal_target / meal_kcal if meal_kcal > 0 else 1
        # model_construct skips validation: these values come from our own
        # arithmetic, and validate_targets checks the portion sizes next.
        meals.append(
            DietMealDraft.model_construct(
                meal_type=meal.meal_type,
                items=[
                    DietItemDraft.model_construct(
                        food_id=item.food_id,
                        grams=float(max(1, round(item.grams * factor))),
                    )
                    for item in meal.items
                ],
            )
        )
    return DietPlanDraft.model_construct(meals=meals)


def is_high_protein(food: Food) -> bool:
    """A food that is a real protein source: enough protein per 100 g, and
    enough of its calories coming from protein."""
    if food.kcal_per_100g <= 0:
        return False
    protein_share = food.protein_per_100g * 4 / food.kcal_per_100g
    return (
        food.protein_per_100g >= HIGH_PROTEIN_MIN_G_PER_100G
        and protein_share >= HIGH_PROTEIN_MIN_CALORIE_SHARE
    )


def rebalance_for_protein(
    draft: DietPlanDraft,
    foods_by_id: Mapping[uuid.UUID, Food],
    target_protein_g: int,
) -> DietPlanDraft:
    """Raise the plan's protein to the target without changing its calories.

    In every meal that has both kinds of food, some calories are moved from
    the low-protein foods to the high-protein foods the model already chose.
    The same share is moved in each meal, and never more than
    MAX_PROTEIN_SHIFT, so no food disappears. If that is not enough, the plan
    is returned as close as it gets and validation reports the gap."""
    current = plan_nutrients(draft, foods_by_id).protein_g
    if round(current) >= target_protein_g:
        return draft
    needed = target_protein_g + PROTEIN_MARGIN_G - current

    # For each meal: (low-protein kcal / high-protein kcal), or None when the
    # meal cannot be rebalanced.
    ratios: list[float | None] = []
    # Protein gained across the plan if all low-protein calories were moved.
    gain_at_full_shift = 0.0
    for meal in draft.meals:
        high = [i for i in meal.items if is_high_protein(foods_by_id[i.food_id])]
        low = [i for i in meal.items if not is_high_protein(foods_by_id[i.food_id])]
        high_total = meal_nutrients(high, foods_by_id)
        low_total = meal_nutrients(low, foods_by_id)
        if high_total.kcal <= 0 or low_total.kcal <= 0:
            ratios.append(None)
            continue
        ratio = low_total.kcal / high_total.kcal
        ratios.append(ratio)
        gain_at_full_shift += ratio * high_total.protein_g - low_total.protein_g

    if gain_at_full_shift <= 0:
        return draft
    shift = min(MAX_PROTEIN_SHIFT, needed / gain_at_full_shift)

    meals = []
    for meal, ratio in zip(draft.meals, ratios):
        if ratio is None:
            meals.append(meal)
            continue
        items = []
        for item in meal.items:
            if is_high_protein(foods_by_id[item.food_id]):
                factor = 1 + shift * ratio
            else:
                factor = 1 - shift
            items.append(
                DietItemDraft.model_construct(
                    food_id=item.food_id,
                    grams=float(max(1, round(item.grams * factor))),
                )
            )
        meals.append(
            DietMealDraft.model_construct(meal_type=meal.meal_type, items=items)
        )
    return DietPlanDraft.model_construct(meals=meals)


def validate_targets(
    draft: DietPlanDraft,
    *,
    foods_by_id: Mapping[uuid.UUID, Food],
    target_kcal: int,
    target_protein_g: int,
) -> list[Issue]:
    """Check the numbers: calories, protein and portion sizes."""
    issues = []
    totals = plan_nutrients(draft, foods_by_id)

    if abs(totals.kcal - target_kcal) > CALORIE_TOLERANCE * target_kcal:
        issues.append(
            Issue(
                RULE_CALORIES,
                f"The plan has {round(totals.kcal)} kcal but must be within "
                f"{round(CALORIE_TOLERANCE * 100)}% of {target_kcal} kcal.",
            )
        )

    # Compare whole grams, the same precision the targets use.
    if round(totals.protein_g) < target_protein_g:
        issues.append(
            Issue(
                RULE_PROTEIN,
                f"With portions scaled to {target_kcal} kcal, the plan has "
                f"{round(totals.protein_g)} g of protein but needs at least "
                f"{target_protein_g} g. Use more high-protein foods and fewer "
                "low-protein ones.",
            )
        )

    for meal in draft.meals:
        for item in meal.items:
            name = foods_by_id[item.food_id].name
            if item.grams > MAX_PORTION_GRAMS:
                issues.append(
                    Issue(
                        RULE_PORTIONS,
                        f"{meal.meal_type}: {round(item.grams)} g of '{name}' "
                        f"is more than {MAX_PORTION_GRAMS} g. Add other foods "
                        "to the meal or use foods with more calories.",
                    )
                )
            elif item.grams < MIN_PORTION_GRAMS:
                issues.append(
                    Issue(
                        RULE_PORTIONS,
                        f"{meal.meal_type}: {round(item.grams)} g of '{name}' "
                        f"is less than {MIN_PORTION_GRAMS} g. Use a larger "
                        "share of it or leave it out.",
                    )
                )

    return issues


def plan_json_schema(allowed_ids: Iterable[uuid.UUID]) -> dict:
    """The JSON schema sent to the LLM: the Pydantic schema, narrowed to four
    meals and to the food ids on offer."""
    schema = DietPlanDraft.model_json_schema()
    schema["properties"]["meals"]["minItems"] = len(REQUIRED_MEALS)
    schema["properties"]["meals"]["maxItems"] = len(REQUIRED_MEALS)
    schema["$defs"]["DietItemDraft"]["properties"]["food_id"] = {
        "type": "string",
        "enum": [str(food_id) for food_id in allowed_ids],
    }
    return schema


def nutrition_tool(foods: Iterable[Food]) -> Tool:
    """A calculator the model can call to check a meal or the whole day
    before it answers. The model picks the foods; this does the arithmetic."""
    foods_by_id = {str(food.id): food for food in foods}

    def calculate(arguments: dict) -> dict:
        total = Nutrients()
        for item in arguments["items"]:
            food = foods_by_id.get(item["food_id"])
            if food is None:
                raise ToolError(
                    f"Food id {item['food_id']} is not in the list of "
                    "available foods."
                )
            if item["grams"] <= 0:
                raise ToolError(f"The portion of '{food.name}' must be above 0 g.")
            total += food_nutrients(food, item["grams"])
        return {
            "kcal": round(total.kcal),
            "protein_g": round(total.protein_g, 1),
            "carbs_g": round(total.carbs_g, 1),
            "fat_g": round(total.fat_g, 1),
        }

    return Tool(
        name=NUTRITION_TOOL_NAME,
        description=(
            "Add up the calories, protein, carbs and fat of a list of foods "
            "and portions. Use it to check a meal or the whole day before you "
            "give your final plan, instead of adding the numbers up yourself. "
            "Takes food ids from the list of available foods and portions in "
            "grams, and returns the totals."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "description": "The foods to add up.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "food_id": {
                                "type": "string",
                                "description": "An id from the list of foods.",
                            },
                            "grams": {
                                "type": "number",
                                "description": "The portion in grams.",
                            },
                        },
                        "required": ["food_id", "grams"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["items"],
            "additionalProperties": False,
        },
        handler=calculate,
    )


def build_messages(
    profile: ProfileRead,
    targets: Targets,
    foods: Iterable[Food],
    with_nutrition_tool: bool = False,
) -> list[Message]:
    food_lines = "\n".join(
        f"{food.id} | {food.name} | {food.kcal_per_100g:g} | "
        f"{food.protein_per_100g:g}"
        for food in foods
    )
    restrictions = ", ".join(profile.dietary_restrictions) or "none"
    user_prompt = f"""\
Build a one-day meal plan for this person.

Goal: {profile.goal.value}
Daily calorie target: {targets.calories} kcal
Daily protein target: at least {targets.protein_g} g
Dietary restrictions: {restrictions}

Available foods (id | name | kcal per 100 g | protein g per 100 g):
{food_lines}"""
    if with_nutrition_tool:
        user_prompt += f"""

Before you answer, check the whole day with the {NUTRITION_TOOL_NAME} tool. If \
the protein is below the target, change the foods or portions and check again."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


async def generate_diet_plan(
    llm: LLMClient,
    *,
    profile: ProfileRead,
    targets: Targets,
    foods: Iterable[Food],
    attempt_log: list[AttemptRecord] | None = None,
) -> GeneratedDietPlan:
    """Ask the LLM for a plan until it passes validation. The returned plan
    already has its portions scaled to the calorie target. Raises
    PlanGenerationError when no attempt is valid, and lets LLMError through
    when the model cannot be reached."""
    foods_by_id = {food.id: food for food in foods}
    # The model is only shown foods the user can eat.
    allowed = allowed_foods(
        foods_by_id.values(), profile.allergies, profile.dietary_restrictions
    )

    def check(draft: DietPlanDraft) -> tuple[DietPlanDraft, list[Issue]]:
        issues = validate_foods(
            draft,
            foods_by_id=foods_by_id,
            allergies=profile.allergies,
            restrictions=profile.dietary_restrictions,
        )
        if issues:
            # Unknown foods or missing meals cannot be scaled, so stop here.
            return draft, issues

        scaled = rescale_to_calories(draft, foods_by_id, targets.calories)
        scaled = rebalance_for_protein(scaled, foods_by_id, targets.protein_g)
        issues = validate_targets(
            scaled,
            foods_by_id=foods_by_id,
            target_kcal=targets.calories,
            target_protein_g=targets.protein_g,
        )
        return scaled, issues

    # A provider that supports tools gets a calculator, so the model can check
    # its numbers. Whatever it answers is still scaled and validated above.
    tools = [nutrition_tool(allowed)] if llm.supports_tools else []

    draft, model, attempts = await generate_valid_plan(
        llm,
        messages=build_messages(
            profile, targets, allowed, with_nutrition_tool=bool(tools)
        ),
        json_schema=plan_json_schema(food.id for food in allowed),
        draft_model=DietPlanDraft,
        check=check,
        tools=tools,
        attempt_log=attempt_log,
    )
    return GeneratedDietPlan(draft=draft, model=model, attempts=attempts)

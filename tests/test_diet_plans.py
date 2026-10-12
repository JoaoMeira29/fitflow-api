import json
import uuid

import pytest
from sqlalchemy import func, select

from app.core.diet_plans import (
    MEAL_CALORIE_SHARES,
    allowed_foods,
    cap_portions,
    food_nutrients,
    is_high_protein,
    meal_nutrients,
    nutrition_tool,
    plan_json_schema,
    plan_nutrients,
    rebalance_for_protein,
    rescale_to_calories,
    validate_foods,
    validate_targets,
)
from app.core.llm import LLMError, ToolError
from app.models.diet_plan import DietPlan, DietPlanMealItem
from app.models.food import Food
from app.schemas.diet import DIET_NOTICE, DietPlanDraft

PROFILE = {
    "goal": "maintain",
    "birth_date": "1995-06-15",
    "sex": "female",
    "height_cm": 165.0,
    "weight_kg": 60.0,
    "activity_level": "light",
    "training_days_per_week": 3,
    "equipment": ["bodyweight"],
    "allergies": [],
    "dietary_restrictions": [],
}

VEGAN = ["vegetarian", "vegan", "pescatarian"]
VEGETARIAN = ["vegetarian", "pescatarian"]

# name: (kcal, protein, carbs, fat, allergens, diet tags), per 100 g
FOODS = {
    "Chicken breast": (120, 22.5, 0, 2.6, [], []),
    "Salmon": (208, 20.4, 0, 13.4, ["fish"], ["pescatarian"]),
    "Egg": (143, 12.6, 0.7, 9.5, ["eggs"], VEGETARIAN),
    "Greek yogurt": (59, 10.2, 3.6, 0.4, ["milk"], VEGETARIAN),
    "Oats": (379, 13.2, 67.7, 6.5, ["gluten"], VEGAN),
    "White rice": (130, 2.7, 28.2, 0.3, [], VEGAN),
    "Broccoli": (34, 2.8, 6.6, 0.4, [], VEGAN),
    "Banana": (89, 1.1, 22.8, 0.3, [], VEGAN),
    "Peanut butter": (588, 25.1, 20, 50, ["peanuts"], VEGAN),
}


# The largest sensible portion of the foods that have a small one, in grams.
# The other foods get 600 g, which no test plan reaches by accident.
MAX_PORTIONS = {"Peanut butter": 40}


@pytest.fixture
async def catalog(db_session) -> dict[str, Food]:
    foods = [
        Food(
            id=uuid.uuid4(),
            name=name,
            kcal_per_100g=kcal,
            protein_per_100g=protein,
            carbs_per_100g=carbs,
            fat_per_100g=fat,
            max_portion_g=MAX_PORTIONS.get(name, 600),
            allergens=allergens,
            diet_tags=diet_tags,
        )
        for name, (kcal, protein, carbs, fat, allergens, diet_tags) in FOODS.items()
    ]
    db_session.add_all(foods)
    await db_session.commit()
    return {food.name: food for food in foods}


def meal(catalog, meal_type: str, **grams_by_food: float) -> dict:
    """meal(catalog, "lunch", Chicken_breast=200) -> one meal as the LLM writes it."""
    return {
        "meal_type": meal_type,
        "items": [
            {"food_id": str(catalog[name.replace("_", " ")].id), "grams": grams}
            for name, grams in grams_by_food.items()
        ],
    }


def plan_json(*meals: dict) -> str:
    return json.dumps({"meals": list(meals)})


def valid_plan(catalog) -> str:
    return plan_json(
        meal(catalog, "breakfast", Egg=100, Oats=40, Greek_yogurt=150),
        meal(catalog, "lunch", Chicken_breast=200, White_rice=150, Broccoli=100),
        meal(catalog, "dinner", Salmon=150, White_rice=150, Broccoli=100),
        meal(catalog, "snack", Greek_yogurt=200, Banana=100),
    )


def low_protein_plan(catalog) -> str:
    """A plan with no protein source at all, so the code cannot repair it."""
    return plan_json(
        meal(catalog, "breakfast", Banana=200, Oats=40),
        meal(catalog, "lunch", White_rice=300, Broccoli=100),
        meal(catalog, "dinner", White_rice=300, Banana=100),
        meal(catalog, "snack", Banana=150, Oats=20),
    )


def carb_heavy_plan(catalog) -> str:
    """Every meal has a protein source, but far too little of it."""
    return plan_json(
        meal(catalog, "breakfast", Egg=50, Oats=100),
        meal(catalog, "lunch", Chicken_breast=60, White_rice=400),
        meal(catalog, "dinner", Salmon=50, White_rice=400),
        meal(catalog, "snack", Greek_yogurt=50, Banana=200),
    )


def draft_of(plan: str) -> DietPlanDraft:
    return DietPlanDraft.model_validate_json(plan)


def by_id(catalog) -> dict[uuid.UUID, Food]:
    return {food.id: food for food in catalog.values()}


def check_foods(catalog, plan: str, allergies=(), restrictions=()) -> list[str]:
    issues = validate_foods(
        draft_of(plan),
        foods_by_id=by_id(catalog),
        allergies=allergies,
        restrictions=restrictions,
    )
    return [issue.message for issue in issues]


@pytest.fixture
async def profile(client, auth_headers):
    await client.put("/profile", json=PROFILE, headers=auth_headers)


@pytest.fixture
async def targets(client, auth_headers, profile) -> dict:
    return (await client.get("/profile/targets", headers=auth_headers)).json()


# --- Nutrients -------------------------------------------------------------


def test_food_nutrients_scale_with_the_portion(catalog):
    nutrients = food_nutrients(catalog["Chicken breast"], 200)

    assert nutrients.kcal == pytest.approx(240)
    assert nutrients.protein_g == pytest.approx(45)
    assert nutrients.fat_g == pytest.approx(5.2)


def test_plan_nutrients_add_up_every_item(catalog):
    plan = plan_json(
        meal(catalog, "lunch", Chicken_breast=100, White_rice=100),
        meal(catalog, "snack", Banana=100, Broccoli=100),
    )

    totals = plan_nutrients(draft_of(plan), by_id(catalog))

    assert totals.kcal == pytest.approx(120 + 130 + 89 + 34)
    assert totals.protein_g == pytest.approx(22.5 + 2.7 + 1.1 + 2.8)


# --- allowed_foods ---------------------------------------------------------


def names(foods) -> set[str]:
    return {food.name for food in foods}


def test_allowed_foods_leave_out_allergens(catalog):
    allowed = allowed_foods(catalog.values(), ["peanuts", "milk"], [])

    assert names(allowed) == set(FOODS) - {"Peanut butter", "Greek yogurt"}


def test_allowed_foods_respect_a_vegetarian_diet(catalog):
    allowed = allowed_foods(catalog.values(), [], ["vegetarian"])

    assert names(allowed) == set(FOODS) - {"Chicken breast", "Salmon"}


def test_allowed_foods_respect_a_vegan_diet(catalog):
    allowed = allowed_foods(catalog.values(), [], ["vegan"])

    assert names(allowed) == {
        "Oats",
        "White rice",
        "Broccoli",
        "Banana",
        "Peanut butter",
    }


def test_allowed_foods_combine_allergies_and_restrictions(catalog):
    allowed = allowed_foods(catalog.values(), ["gluten", "peanuts"], ["vegan"])

    assert names(allowed) == {"White rice", "Broccoli", "Banana"}


# --- validate_foods --------------------------------------------------------


def test_a_valid_plan_has_no_food_errors(catalog):
    assert check_foods(catalog, valid_plan(catalog)) == []


def test_a_missing_meal_is_an_error(catalog):
    plan = plan_json(
        meal(catalog, "breakfast", Egg=100, Oats=40),
        meal(catalog, "lunch", Chicken_breast=200, White_rice=150),
        meal(catalog, "dinner", Salmon=150, White_rice=150),
    )

    errors = check_foods(catalog, plan)

    assert errors == [
        "The plan must have exactly four meals: breakfast, lunch, dinner and "
        "snack. It has: breakfast, lunch, dinner."
    ]


def test_a_repeated_meal_type_is_an_error(catalog):
    plan = plan_json(
        meal(catalog, "breakfast", Egg=100, Oats=40),
        meal(catalog, "lunch", Chicken_breast=200, White_rice=150),
        meal(catalog, "lunch", Salmon=150, White_rice=150),
        meal(catalog, "snack", Greek_yogurt=200, Banana=100),
    )

    assert len(check_foods(catalog, plan)) == 1


def test_an_unknown_food_id_is_an_error(catalog):
    plan = json.loads(valid_plan(catalog))
    invented = str(uuid.uuid4())
    plan["meals"][0]["items"][0]["food_id"] = invented

    errors = check_foods(catalog, json.dumps(plan))

    assert errors == [
        f"breakfast: food id {invented} is not in the list of available foods."
    ]


def test_a_food_with_an_allergen_is_an_error(catalog):
    errors = check_foods(catalog, valid_plan(catalog), allergies=["fish"])

    assert errors == [
        "dinner: 'Salmon' contains an allergen the user must avoid (fish)."
    ]


def test_a_food_outside_the_diet_is_an_error(catalog):
    errors = check_foods(catalog, valid_plan(catalog), restrictions=["vegetarian"])

    assert errors == [
        "lunch: 'Chicken breast' is not suitable for a vegetarian diet.",
        "dinner: 'Salmon' is not suitable for a vegetarian diet.",
    ]


def test_a_repeated_food_in_a_meal_is_an_error(catalog):
    plan = json.loads(valid_plan(catalog))
    plan["meals"][3]["items"][1]["food_id"] = plan["meals"][3]["items"][0]["food_id"]

    errors = check_foods(catalog, json.dumps(plan))

    assert errors == ["snack: 'Greek yogurt' appears more than once."]


# --- rescale_to_calories ---------------------------------------------------


def test_rescale_hits_the_calorie_target(catalog):
    scaled = rescale_to_calories(draft_of(valid_plan(catalog)), by_id(catalog), 2000)

    # Rounding portions to whole grams moves the total by a few kcal at most.
    assert plan_nutrients(scaled, by_id(catalog)).kcal == pytest.approx(2000, abs=10)


def test_rescale_gives_each_meal_its_share_of_the_day(catalog):
    scaled = rescale_to_calories(draft_of(valid_plan(catalog)), by_id(catalog), 2000)

    for scaled_meal in scaled.meals:
        kcal = plan_nutrients(
            DietPlanDraft.model_construct(meals=[scaled_meal]), by_id(catalog)
        ).kcal
        expected = 2000 * MEAL_CALORIE_SHARES[scaled_meal.meal_type]
        assert kcal == pytest.approx(expected, abs=5)


def test_rescale_keeps_the_proportions_inside_a_meal(catalog):
    plan = plan_json(
        meal(catalog, "breakfast", Egg=100, Oats=50),
        meal(catalog, "lunch", Chicken_breast=200, White_rice=100),
        meal(catalog, "dinner", Salmon=100, White_rice=100),
        meal(catalog, "snack", Greek_yogurt=200, Banana=100),
    )

    scaled = rescale_to_calories(draft_of(plan), by_id(catalog), 3000)

    lunch = scaled.meals[1].items
    assert lunch[0].grams / lunch[1].grams == pytest.approx(2, rel=0.01)


def test_rescale_can_shrink_a_plan_as_well(catalog):
    scaled = rescale_to_calories(draft_of(valid_plan(catalog)), by_id(catalog), 1200)

    assert plan_nutrients(scaled, by_id(catalog)).kcal == pytest.approx(1200, abs=10)


# --- rebalance_for_protein -------------------------------------------------


def test_high_protein_foods_need_density_and_calorie_share(catalog):
    assert is_high_protein(catalog["Chicken breast"]) is True
    assert is_high_protein(catalog["Egg"]) is True
    assert is_high_protein(catalog["Greek yogurt"]) is True
    # A third of broccoli's calories are protein, but only 2.8 g per 100 g.
    assert is_high_protein(catalog["Broccoli"]) is False
    # Peanut butter has 25 g per 100 g, but only 17% of its calories.
    assert is_high_protein(catalog["Peanut butter"]) is False
    assert is_high_protein(catalog["White rice"]) is False


def test_rebalance_raises_protein_to_the_target(catalog):
    scaled = rescale_to_calories(draft_of(carb_heavy_plan(catalog)), by_id(catalog), 2000)
    assert plan_nutrients(scaled, by_id(catalog)).protein_g < 100

    rebalanced = rebalance_for_protein(scaled, by_id(catalog), 100)

    assert round(plan_nutrients(rebalanced, by_id(catalog)).protein_g) >= 100


def test_rebalance_keeps_the_calories_of_every_meal(catalog):
    scaled = rescale_to_calories(draft_of(carb_heavy_plan(catalog)), by_id(catalog), 2000)

    rebalanced = rebalance_for_protein(scaled, by_id(catalog), 100)

    for before, after in zip(scaled.meals, rebalanced.meals):
        kcal_before = meal_nutrients(before.items, by_id(catalog)).kcal
        kcal_after = meal_nutrients(after.items, by_id(catalog)).kcal
        assert kcal_after == pytest.approx(kcal_before, abs=5)


def test_rebalance_moves_calories_from_low_to_high_protein_foods(catalog):
    scaled = rescale_to_calories(draft_of(carb_heavy_plan(catalog)), by_id(catalog), 2000)

    rebalanced = rebalance_for_protein(scaled, by_id(catalog), 100)

    chicken_before, rice_before = scaled.meals[1].items
    chicken_after, rice_after = rebalanced.meals[1].items
    assert chicken_after.grams > chicken_before.grams
    assert rice_after.grams < rice_before.grams


def test_rebalance_leaves_a_plan_with_enough_protein_alone(catalog):
    scaled = rescale_to_calories(draft_of(valid_plan(catalog)), by_id(catalog), 2000)

    assert rebalance_for_protein(scaled, by_id(catalog), 100) is scaled


def test_rebalance_moves_at_most_half_of_the_low_protein_calories(catalog):
    scaled = rescale_to_calories(draft_of(carb_heavy_plan(catalog)), by_id(catalog), 2000)

    # 400 g of protein is out of reach, so the shift stops at its limit.
    rebalanced = rebalance_for_protein(scaled, by_id(catalog), 400)

    rice_before = scaled.meals[1].items[1].grams
    rice_after = rebalanced.meals[1].items[1].grams
    assert rice_after == pytest.approx(rice_before * 0.5, abs=1)
    assert plan_nutrients(rebalanced, by_id(catalog)).protein_g < 400


def test_rebalance_cannot_fix_a_plan_without_protein_sources(catalog):
    scaled = rescale_to_calories(
        draft_of(low_protein_plan(catalog)), by_id(catalog), 2000
    )

    assert rebalance_for_protein(scaled, by_id(catalog), 100) is scaled


# --- cap_portions ----------------------------------------------------------


def grams_of(plan: DietPlanDraft, meal_index: int) -> list[float]:
    return [item.grams for item in plan.meals[meal_index].items]


def test_cap_brings_a_portion_down_to_its_limit_and_keeps_the_calories(catalog):
    # 100 g of peanut butter is far above its 40 g limit.
    plan = draft_of(plan_json(meal(catalog, "snack", Peanut_butter=100, Banana=100)))
    before = meal_nutrients(plan.meals[0].items, by_id(catalog)).kcal

    capped = cap_portions(plan, by_id(catalog))

    peanut_butter, banana = grams_of(capped, 0)
    assert peanut_butter == 40
    assert banana > 100  # the banana makes up the calories
    after = meal_nutrients(capped.meals[0].items, by_id(catalog)).kcal
    assert after == pytest.approx(before, abs=3)


def test_cap_leaves_a_plan_without_oversized_portions_alone(catalog):
    plan = draft_of(valid_plan(catalog))

    capped = cap_portions(plan, by_id(catalog))

    assert capped.meals == plan.meals


def test_cap_only_changes_the_meal_with_the_oversized_portion(catalog):
    plan = draft_of(
        plan_json(
            meal(catalog, "lunch", Chicken_breast=200, White_rice=150),
            meal(catalog, "snack", Peanut_butter=100, Banana=100),
        )
    )

    capped = cap_portions(plan, by_id(catalog))

    assert grams_of(capped, 0) == [200, 150]
    assert grams_of(capped, 1)[0] == 40


def test_cap_repeats_when_the_extra_pushes_another_food_over(catalog):
    # 500 g of peanut butter is 2940 kcal. Capped to 40 g, the banana would
    # need over 3 kg to make that up, far beyond its own 600 g limit.
    plan = draft_of(plan_json(meal(catalog, "snack", Peanut_butter=500, Banana=100)))

    capped = cap_portions(plan, by_id(catalog))

    assert grams_of(capped, 0) == [40, 600]


def test_a_plan_capped_at_every_food_fails_validation_on_calories(catalog):
    plan = draft_of(
        plan_json(
            meal(catalog, "breakfast", Egg=100, Oats=40),
            meal(catalog, "lunch", Chicken_breast=200, White_rice=150),
            meal(catalog, "dinner", Salmon=150, White_rice=150),
            meal(catalog, "snack", Peanut_butter=500, Banana=100),
        )
    )
    target = round(plan_nutrients(plan, by_id(catalog)).kcal)

    capped = cap_portions(plan, by_id(catalog))

    errors = check_targets(catalog, capped, kcal=target, protein=10)
    assert len(errors) == 1
    assert "must be within 5%" in errors[0]


# --- validate_targets ------------------------------------------------------


def check_targets(catalog, plan: DietPlanDraft, kcal=2000, protein=100) -> list[str]:
    issues = validate_targets(
        plan, foods_by_id=by_id(catalog), target_kcal=kcal, target_protein_g=protein
    )
    return [issue.message for issue in issues]


def test_a_scaled_plan_with_enough_protein_has_no_errors(catalog):
    scaled = rescale_to_calories(draft_of(valid_plan(catalog)), by_id(catalog), 2000)

    assert check_targets(catalog, scaled) == []


def test_calories_more_than_5_percent_off_are_an_error(catalog):
    scaled = rescale_to_calories(draft_of(valid_plan(catalog)), by_id(catalog), 2000)

    errors = check_targets(catalog, scaled, kcal=2200)

    assert len(errors) == 1
    assert "must be within 5% of 2200 kcal" in errors[0]


def test_calories_within_5_percent_are_accepted(catalog):
    scaled = rescale_to_calories(draft_of(valid_plan(catalog)), by_id(catalog), 2000)

    assert check_targets(catalog, scaled, kcal=2090) == []


def test_protein_below_the_target_is_an_error(catalog):
    scaled = rescale_to_calories(
        draft_of(low_protein_plan(catalog)), by_id(catalog), 2000
    )

    errors = check_targets(catalog, scaled)

    assert len(errors) == 1
    assert "needs at least 100 g" in errors[0]


def test_a_portion_over_the_limit_is_an_error(catalog):
    plan = plan_json(
        meal(catalog, "breakfast", Egg=100, Oats=40),
        meal(catalog, "lunch", Chicken_breast=200, Broccoli=700),
        meal(catalog, "dinner", Salmon=150, White_rice=150),
        meal(catalog, "snack", Greek_yogurt=200, Banana=100),
    )

    errors = check_targets(catalog, draft_of(plan), kcal=1490, protein=50)

    assert errors == [
        "lunch: 700 g of 'Broccoli' is more than a sensible portion of 600 g. "
        "Add other foods to the meal or use foods with more calories."
    ]


def test_a_portion_under_the_limit_is_an_error(catalog):
    plan = plan_json(
        meal(catalog, "breakfast", Egg=100, Oats=40),
        meal(catalog, "lunch", Chicken_breast=200, White_rice=150),
        meal(catalog, "dinner", Salmon=150, White_rice=150),
        meal(catalog, "snack", Greek_yogurt=200, Peanut_butter=2),
    )

    errors = check_targets(catalog, draft_of(plan), kcal=1400, protein=50)

    assert errors == [
        "snack: 2 g of 'Peanut butter' is less than 5 g. Use a larger share of "
        "it or leave it out."
    ]


# --- plan_json_schema ------------------------------------------------------


def test_schema_only_allows_the_offered_foods_and_four_meals():
    ids = [uuid.uuid4(), uuid.uuid4()]

    schema = plan_json_schema(ids)

    food_id = schema["$defs"]["DietItemDraft"]["properties"]["food_id"]
    assert food_id["enum"] == [str(ids[0]), str(ids[1])]
    assert schema["properties"]["meals"]["minItems"] == 4
    assert schema["properties"]["meals"]["maxItems"] == 4


# --- nutrition_tool --------------------------------------------------------


def tool_items(catalog, **grams_by_food: float) -> dict:
    return {
        "items": [
            {"food_id": str(catalog[name.replace("_", " ")].id), "grams": grams}
            for name, grams in grams_by_food.items()
        ]
    }


def test_nutrition_tool_adds_up_the_foods(catalog):
    tool = nutrition_tool(catalog.values())

    result = tool.handler(tool_items(catalog, Chicken_breast=200, White_rice=150))

    assert result == {
        "kcal": 435,  # 240 + 195
        "protein_g": 49.0,  # 45 + 4.05, rounded
        "carbs_g": 42.3,
        "fat_g": 5.7,
    }


def test_nutrition_tool_rejects_a_food_that_is_not_on_offer(catalog):
    tool = nutrition_tool([catalog["White rice"]])

    with pytest.raises(ToolError, match="not in the list of available foods"):
        tool.handler(tool_items(catalog, Chicken_breast=200))


def test_nutrition_tool_rejects_a_portion_of_zero(catalog):
    tool = nutrition_tool(catalog.values())

    with pytest.raises(ToolError, match="must be above 0 g"):
        tool.handler(tool_items(catalog, White_rice=0))


def test_nutrition_tool_schema_is_strict(catalog):
    schema = nutrition_tool(catalog.values()).input_schema

    assert schema["additionalProperties"] is False
    assert schema["required"] == ["items"]
    item = schema["properties"]["items"]["items"]
    assert item["additionalProperties"] is False
    assert item["required"] == ["food_id", "grams"]


async def test_post_offers_the_nutrition_tool_when_the_provider_supports_tools(
    client, auth_headers, catalog, profile, fake_llm
):
    fake_llm.supports_tools = True
    fake_llm.replies = [valid_plan(catalog)]

    response = await client.post("/plans/diet", headers=auth_headers)

    assert response.status_code == 201
    call = fake_llm.calls[0]
    assert [tool.name for tool in call["tools"]] == ["calculate_nutrition"]
    assert "calculate_nutrition tool" in call["messages"][1]["content"]


async def test_post_only_lets_the_tool_use_foods_the_user_can_eat(
    client, auth_headers, catalog, fake_llm
):
    await client.put(
        "/profile", json={**PROFILE, "allergies": ["peanuts"]}, headers=auth_headers
    )
    fake_llm.supports_tools = True
    fake_llm.replies = [valid_plan(catalog)]

    await client.post("/plans/diet", headers=auth_headers)

    tool = fake_llm.calls[0]["tools"][0]
    with pytest.raises(ToolError):
        tool.handler(tool_items(catalog, Peanut_butter=20))


async def test_post_offers_no_tool_when_the_provider_does_not_support_tools(
    client, auth_headers, catalog, profile, fake_llm
):
    fake_llm.replies = [valid_plan(catalog)]

    await client.post("/plans/diet", headers=auth_headers)

    call = fake_llm.calls[0]
    assert call["tools"] == []
    assert "calculate_nutrition" not in call["messages"][1]["content"]


# --- POST /plans/diet ------------------------------------------------------


async def test_post_creates_and_returns_the_plan(
    client, auth_headers, catalog, targets, fake_llm
):
    fake_llm.replies = [valid_plan(catalog)]

    response = await client.post("/plans/diet", headers=auth_headers)

    assert response.status_code == 201
    body = response.json()
    assert body["is_active"] is True
    assert body["model"] == "fake-model"
    assert body["notice"] == DIET_NOTICE
    assert body["targets"] == {
        "kcal": targets["calories"],
        "protein_g": targets["protein_g"],
        "carbs_g": targets["carbs_g"],
        "fat_g": targets["fat_g"],
    }
    assert [m["meal_type"] for m in body["meals"]] == [
        "breakfast",
        "lunch",
        "dinner",
        "snack",
    ]
    assert [m["position"] for m in body["meals"]] == [1, 2, 3, 4]
    first = body["meals"][0]["items"][0]
    assert first["position"] == 1
    assert first["food"]["name"] == "Egg"
    assert first["food"]["id"] == str(catalog["Egg"].id)


async def test_post_scales_the_portions_to_the_calorie_target(
    client, auth_headers, catalog, targets, fake_llm
):
    fake_llm.replies = [valid_plan(catalog)]

    body = (await client.post("/plans/diet", headers=auth_headers)).json()

    assert body["totals"]["kcal"] == pytest.approx(targets["calories"], abs=10)
    assert body["totals"]["protein_g"] >= targets["protein_g"]
    lunch = body["meals"][1]
    assert lunch["totals"]["kcal"] == pytest.approx(0.35 * targets["calories"], abs=5)
    # The model wrote 200 g of chicken; the stored portion is the scaled one.
    assert lunch["items"][0]["grams"] != 200


async def test_post_computes_the_totals_in_code(
    client, auth_headers, catalog, targets, fake_llm
):
    fake_llm.replies = [valid_plan(catalog)]

    body = (await client.post("/plans/diet", headers=auth_headers)).json()

    for plan_meal in body["meals"]:
        for item in plan_meal["items"]:
            expected = item["food"]["kcal_per_100g"] * item["grams"] / 100
            assert item["nutrients"]["kcal"] == round(expected)
        item_kcal = sum(item["nutrients"]["kcal"] for item in plan_meal["items"])
        assert plan_meal["totals"]["kcal"] == pytest.approx(item_kcal, abs=3)
    meal_kcal = sum(plan_meal["totals"]["kcal"] for plan_meal in body["meals"])
    assert body["totals"]["kcal"] == pytest.approx(meal_kcal, abs=4)


async def test_post_only_offers_foods_the_user_can_eat(
    client, auth_headers, catalog, fake_llm
):
    await client.put(
        "/profile",
        json={
            **PROFILE,
            "allergies": ["peanuts"],
            "dietary_restrictions": ["vegetarian"],
        },
        headers=auth_headers,
    )
    fake_llm.replies = [low_protein_plan(catalog)] * 3

    await client.post("/plans/diet", headers=auth_headers)

    call = fake_llm.calls[0]
    prompt = call["messages"][1]["content"]
    assert "Greek yogurt" in prompt
    assert "Chicken breast" not in prompt
    assert "Salmon" not in prompt
    assert "Peanut butter" not in prompt
    assert "Dietary restrictions: vegetarian" in prompt
    allowed = call["json_schema"]["$defs"]["DietItemDraft"]["properties"]["food_id"][
        "enum"
    ]
    assert len(allowed) == len(FOODS) - 3


async def test_post_tells_the_model_the_targets(
    client, auth_headers, catalog, targets, fake_llm
):
    fake_llm.replies = [valid_plan(catalog)]

    await client.post("/plans/diet", headers=auth_headers)

    prompt = fake_llm.calls[0]["messages"][1]["content"]
    assert f"Daily calorie target: {targets['calories']} kcal" in prompt
    assert f"Daily protein target: at least {targets['protein_g']} g" in prompt


async def test_post_accepts_a_calorie_target_from_the_user(
    client, auth_headers, catalog, targets, fake_llm
):
    fake_llm.replies = [valid_plan(catalog)]

    response = await client.post(
        "/plans/diet", json={"target_kcal": 1500}, headers=auth_headers
    )

    assert response.status_code == 201
    body = response.json()
    assert body["targets"]["kcal"] == 1500
    assert body["targets"]["kcal"] != targets["calories"]
    # Protein follows body weight, so it does not change with the calories.
    assert body["targets"]["protein_g"] == targets["protein_g"]
    assert body["totals"]["kcal"] == pytest.approx(1500, abs=10)
    prompt = fake_llm.calls[0]["messages"][1]["content"]
    assert "Daily calorie target: 1500 kcal" in prompt


async def test_post_without_a_calorie_target_uses_the_profile(
    client, auth_headers, catalog, targets, fake_llm
):
    fake_llm.replies = [valid_plan(catalog)]

    response = await client.post("/plans/diet", json={}, headers=auth_headers)

    assert response.status_code == 201
    assert response.json()["targets"]["kcal"] == targets["calories"]


@pytest.mark.parametrize("target_kcal", [1199, 3000])
async def test_post_rejects_a_calorie_target_outside_the_safety_limits(
    client, auth_headers, catalog, profile, fake_llm, target_kcal
):
    response = await client.post(
        "/plans/diet", json={"target_kcal": target_kcal}, headers=auth_headers
    )

    assert response.status_code == 422
    assert "the calorie target must be between 1200 and" in response.json()["detail"]
    assert fake_llm.calls == []


async def test_post_rejects_a_calorie_target_that_is_not_a_number(
    client, auth_headers, catalog, profile, fake_llm
):
    response = await client.post(
        "/plans/diet", json={"target_kcal": "low"}, headers=auth_headers
    )

    assert response.status_code == 422


async def test_post_stores_the_targets_and_a_profile_snapshot(
    client, auth_headers, catalog, targets, fake_llm, db_session
):
    fake_llm.replies = [valid_plan(catalog)]

    await client.post("/plans/diet", headers=auth_headers)

    plan = await db_session.scalar(select(DietPlan))
    assert plan.target_kcal == targets["calories"]
    assert plan.target_protein_g == targets["protein_g"]
    assert plan.profile_snapshot == PROFILE


async def test_post_caps_an_oversized_portion_in_code_without_a_retry(
    client, auth_headers, catalog, targets, fake_llm
):
    plan = json.loads(valid_plan(catalog))
    # A snack that is nearly all peanut butter.
    plan["meals"][3] = meal(catalog, "snack", Peanut_butter=100, Banana=20)
    fake_llm.replies = [json.dumps(plan)]

    response = await client.post("/plans/diet", headers=auth_headers)

    assert response.status_code == 201
    assert len(fake_llm.calls) == 1
    body = response.json()
    snack = body["meals"][3]
    assert snack["items"][0]["food"]["name"] == "Peanut butter"
    assert snack["items"][0]["grams"] <= 40
    assert snack["totals"]["kcal"] == pytest.approx(0.10 * targets["calories"], abs=5)
    assert body["totals"]["kcal"] == pytest.approx(targets["calories"], abs=15)


async def test_post_repairs_low_protein_in_code_without_a_retry(
    client, auth_headers, catalog, targets, fake_llm
):
    fake_llm.replies = [carb_heavy_plan(catalog)]

    response = await client.post("/plans/diet", headers=auth_headers)

    assert response.status_code == 201
    assert len(fake_llm.calls) == 1
    body = response.json()
    assert body["totals"]["protein_g"] >= targets["protein_g"]
    assert body["totals"]["kcal"] == pytest.approx(targets["calories"], abs=15)


async def test_post_retries_when_protein_is_too_low(
    client, auth_headers, catalog, profile, fake_llm
):
    fake_llm.replies = [low_protein_plan(catalog), valid_plan(catalog)]

    response = await client.post("/plans/diet", headers=auth_headers)

    assert response.status_code == 201
    assert len(fake_llm.calls) == 2
    retry_messages = fake_llm.calls[1]["messages"]
    assert retry_messages[-2]["role"] == "assistant"
    assert "g of protein but needs at least" in retry_messages[-1]["content"]


async def test_post_retries_when_the_reply_does_not_match_the_schema(
    client, auth_headers, catalog, profile, fake_llm
):
    fake_llm.replies = ["this is not JSON", valid_plan(catalog)]

    response = await client.post("/plans/diet", headers=auth_headers)

    assert response.status_code == 201
    assert len(fake_llm.calls) == 2


async def test_post_gives_up_after_two_retries(
    client, auth_headers, catalog, profile, fake_llm, db_session
):
    fake_llm.replies = [low_protein_plan(catalog)] * 3 + [valid_plan(catalog)]

    response = await client.post("/plans/diet", headers=auth_headers)

    assert response.status_code == 502
    assert len(fake_llm.calls) == 3
    assert len(response.json()["detail"]["errors"]) == 1
    assert await db_session.scalar(select(func.count()).select_from(DietPlan)) == 0


async def test_post_returns_503_when_the_llm_is_unavailable(
    client, auth_headers, catalog, profile, fake_llm, db_session
):
    fake_llm.replies = [LLMError("could not connect")]

    response = await client.post("/plans/diet", headers=auth_headers)

    assert response.status_code == 503
    assert await db_session.scalar(select(func.count()).select_from(DietPlan)) == 0


async def test_post_requires_a_profile(client, auth_headers, catalog, fake_llm):
    response = await client.post("/plans/diet", headers=auth_headers)

    assert response.status_code == 409
    assert response.json()["detail"] == "Complete your profile before generating a plan"
    assert fake_llm.calls == []


async def test_post_requires_a_token(client, fake_llm):
    response = await client.post("/plans/diet")

    assert response.status_code == 401


async def test_a_new_plan_deactivates_the_previous_one(
    client, auth_headers, catalog, profile, fake_llm
):
    fake_llm.replies = [valid_plan(catalog), valid_plan(catalog)]

    first = (await client.post("/plans/diet", headers=auth_headers)).json()
    second = (await client.post("/plans/diet", headers=auth_headers)).json()

    plans = (await client.get("/plans/diet", headers=auth_headers)).json()
    active = {plan["id"]: plan["is_active"] for plan in plans}
    assert active == {first["id"]: False, second["id"]: True}


# --- GET /plans/diet -------------------------------------------------------


async def test_list_returns_the_newest_plan_first(
    client, auth_headers, catalog, targets, fake_llm
):
    fake_llm.replies = [valid_plan(catalog), valid_plan(catalog)]
    first = (await client.post("/plans/diet", headers=auth_headers)).json()
    second = (await client.post("/plans/diet", headers=auth_headers)).json()

    response = await client.get("/plans/diet", headers=auth_headers)

    assert response.status_code == 200
    assert [plan["id"] for plan in response.json()] == [second["id"], first["id"]]
    assert response.json()[0]["targets"]["kcal"] == targets["calories"]
    assert "meals" not in response.json()[0]


async def test_list_is_empty_without_plans(client, auth_headers):
    response = await client.get("/plans/diet", headers=auth_headers)

    assert response.json() == []


async def test_list_requires_a_token(client):
    response = await client.get("/plans/diet")

    assert response.status_code == 401


# --- GET /plans/diet/{id} --------------------------------------------------


async def test_get_returns_the_full_plan(
    client, auth_headers, catalog, profile, fake_llm
):
    fake_llm.replies = [valid_plan(catalog)]
    created = (await client.post("/plans/diet", headers=auth_headers)).json()

    response = await client.get(f"/plans/diet/{created['id']}", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == created


async def test_get_returns_404_for_an_unknown_plan(client, auth_headers):
    response = await client.get(f"/plans/diet/{uuid.uuid4()}", headers=auth_headers)

    assert response.status_code == 404
    assert response.json()["detail"] == "Diet plan not found"


async def test_get_returns_404_for_another_users_plan(
    client, auth_headers, auth_headers_for, catalog, profile, fake_llm
):
    fake_llm.replies = [valid_plan(catalog)]
    created = (await client.post("/plans/diet", headers=auth_headers)).json()
    rui = await auth_headers_for("rui@example.com")

    response = await client.get(f"/plans/diet/{created['id']}", headers=rui)

    assert response.status_code == 404


# --- Database --------------------------------------------------------------


async def test_deleting_a_plan_deletes_its_meals_and_items(
    client, auth_headers, catalog, profile, fake_llm, db_session
):
    fake_llm.replies = [valid_plan(catalog)]
    await client.post("/plans/diet", headers=auth_headers)

    plan = await db_session.scalar(select(DietPlan))
    await db_session.delete(plan)
    await db_session.commit()

    count = select(func.count()).select_from(DietPlanMealItem)
    assert await db_session.scalar(count) == 0

from enum import StrEnum

# The fixed choices a profile can use. They are stored in the database as
# plain strings, so adding a new option is a code change with no migration.


class Goal(StrEnum):
    LOSE_WEIGHT = "lose_weight"
    MAINTAIN = "maintain"
    GAIN_MUSCLE = "gain_muscle"


class Sex(StrEnum):
    # Mifflin-St Jeor has one formula for each of these two values.
    MALE = "male"
    FEMALE = "female"


class ActivityLevel(StrEnum):
    SEDENTARY = "sedentary"
    LIGHT = "light"
    MODERATE = "moderate"
    ACTIVE = "active"
    VERY_ACTIVE = "very_active"


class Equipment(StrEnum):
    BODYWEIGHT = "bodyweight"
    DUMBBELLS = "dumbbells"
    BARBELL = "barbell"
    KETTLEBELL = "kettlebell"
    RESISTANCE_BANDS = "resistance_bands"
    PULL_UP_BAR = "pull_up_bar"
    BENCH = "bench"
    CABLE_MACHINE = "cable_machine"
    MACHINES = "machines"


class Allergen(StrEnum):
    # The 14 allergens that EU food labelling rules require to be declared.
    GLUTEN = "gluten"
    CRUSTACEANS = "crustaceans"
    EGGS = "eggs"
    FISH = "fish"
    PEANUTS = "peanuts"
    SOY = "soy"
    MILK = "milk"
    TREE_NUTS = "tree_nuts"
    CELERY = "celery"
    MUSTARD = "mustard"
    SESAME = "sesame"
    SULPHITES = "sulphites"
    LUPIN = "lupin"
    MOLLUSCS = "molluscs"


class DietaryRestriction(StrEnum):
    VEGETARIAN = "vegetarian"
    VEGAN = "vegan"
    PESCATARIAN = "pescatarian"

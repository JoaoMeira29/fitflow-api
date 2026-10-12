from datetime import date

MIN_AGE = 18
MAX_AGE = 100


def calculate_age(birth_date: date, today: date | None = None) -> int:
    """Age in whole years on `today` (defaults to the current date)."""
    today = today or date.today()
    # Subtract one if this year's birthday has not happened yet.
    had_birthday = (today.month, today.day) >= (birth_date.month, birth_date.day)
    return today.year - birth_date.year - (0 if had_birthday else 1)

"""Summarise what the user logged, as pure functions.

The summary is shown by GET /progress and added to the prompt when a plan is
regenerated, so the new plan reflects what the user actually did.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

# How far back the logs count when a plan is regenerated.
PROMPT_WINDOW_DAYS = 28


@dataclass(frozen=True)
class ProgressSummary:
    window_days: int
    # The first and the latest weight logged inside the window.
    start_weight_kg: float | None
    current_weight_kg: float | None
    # Latest minus first; None with fewer than two logs.
    weight_change_kg: float | None
    workouts_completed: int
    # Training days per week over the window; None without a profile.
    workouts_planned: int | None

    @property
    def has_data(self) -> bool:
        return self.current_weight_kg is not None or self.workouts_completed > 0


def summarize_progress(
    weights: Iterable[tuple[date, float]],
    workout_dates: Iterable[date],
    *,
    today: date,
    window_days: int,
    training_days_per_week: int | None,
) -> ProgressSummary:
    """Summarise the logs from the last `window_days` days, today included.
    `weights` are (date, kg) pairs in any order."""
    first_day = today - timedelta(days=window_days - 1)

    in_window = sorted(
        (day, kg) for day, kg in weights if first_day <= day <= today
    )
    start = in_window[0][1] if in_window else None
    current = in_window[-1][1] if in_window else None
    change = round(current - start, 1) if len(in_window) >= 2 else None

    completed = sum(1 for day in workout_dates if first_day <= day <= today)
    planned = (
        round(training_days_per_week * window_days / 7)
        if training_days_per_week is not None
        else None
    )

    return ProgressSummary(
        window_days=window_days,
        start_weight_kg=start,
        current_weight_kg=current,
        weight_change_kg=change,
        workouts_completed=completed,
        workouts_planned=planned,
    )


def progress_prompt(summary: ProgressSummary | None) -> str:
    """The text added to a plan prompt, or "" when there is nothing to say."""
    if summary is None or not summary.has_data:
        return ""

    lines = [f"Recent progress (last {summary.window_days} days):"]
    if summary.weight_change_kg is not None:
        lines.append(
            f"- Weight: {summary.start_weight_kg:g} kg -> "
            f"{summary.current_weight_kg:g} kg ({summary.weight_change_kg:+g} kg)"
        )
    elif summary.current_weight_kg is not None:
        lines.append(f"- Weight: {summary.current_weight_kg:g} kg")
    if summary.workouts_planned is not None:
        lines.append(
            f"- Workouts completed: {summary.workouts_completed} of about "
            f"{summary.workouts_planned} planned"
        )
    else:
        lines.append(f"- Workouts completed: {summary.workouts_completed}")
    lines.append("Take this into account when you build the plan.")
    return "\n".join(lines)

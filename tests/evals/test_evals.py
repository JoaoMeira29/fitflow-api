"""Evals: generate plans for every profile with the REAL model.

They are slow and need Ollama running, so plain `pytest` skips them.

    pytest -m evals -s                  # all profiles, about 20 to 30 minutes
    $env:EVAL_LIMIT = 3; pytest -m evals -s    # a quick check (PowerShell)

A quick check prints its report but does not save it, so it cannot replace
the report of a full run.
"""

import pytest

from tests.evals.harness import eval_diet, eval_workout, success_rate

pytestmark = pytest.mark.evals

# The run fails if fewer profiles than this end with a valid plan.
MIN_SUCCESS_RATE = 0.9


def _progress(kind: str, index: int, total: int, result) -> None:
    status = "ok" if result.valid else "FAILED"
    print(
        f"[{kind} {index}/{total}] {result.profile}: {status}, "
        f"{result.attempts} attempt(s), {result.seconds:.0f} s",
        flush=True,
    )


async def test_workout_plans(eval_profiles, exercises, llm, eval_report):
    results = []
    for index, case in enumerate(eval_profiles, start=1):
        result = await eval_workout(llm, case, exercises)
        results.append(result)
        _progress("workout", index, len(eval_profiles), result)
    eval_report["workout"] = results

    assert success_rate(results) >= MIN_SUCCESS_RATE


async def test_diet_plans(eval_profiles, foods, llm, eval_report):
    results = []
    for index, case in enumerate(eval_profiles, start=1):
        result = await eval_diet(llm, case, foods)
        results.append(result)
        _progress("diet", index, len(eval_profiles), result)
    eval_report["diet"] = results

    assert success_rate(results) >= MIN_SUCCESS_RATE

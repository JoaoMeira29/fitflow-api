import os
import re
from datetime import date
from pathlib import Path

import pytest

from app.core.llm import get_llm_client
from tests.evals.harness import load_exercises, load_foods, load_profiles, render_report

RESULTS_DIR = Path(__file__).resolve().parent / "results"


# The evals read the catalogs from their JSON files and never touch the
# database, so the two database fixtures from tests/conftest.py are replaced
# with empty ones. Ollama is all that needs to be running.
@pytest.fixture(scope="session", autouse=True)
def prepare_database():
    yield


@pytest.fixture(autouse=True)
def clean_tables():
    yield


@pytest.fixture(scope="session")
def eval_profiles():
    # EVAL_LIMIT=3 runs only the first three profiles, for a quick check.
    limit = os.environ.get("EVAL_LIMIT")
    return load_profiles(int(limit) if limit else None)


@pytest.fixture(scope="session")
def exercises():
    return load_exercises()


@pytest.fixture(scope="session")
def foods():
    return load_foods()


@pytest.fixture(scope="session")
def llm():
    return get_llm_client()


@pytest.fixture(scope="session")
def eval_report(llm):
    """Collects the results of each eval and, when the run ends, prints one
    Markdown report and saves it under a name made of the date and the model."""
    results = {}
    yield results
    if not results:
        return
    report = render_report(
        model=llm.model,
        run_date=date.today(),
        workout=results.get("workout"),
        diet=results.get("diet"),
    )
    if os.environ.get("EVAL_LIMIT"):
        # A quick run on a few profiles must not replace a full report.
        print(f"\nPartial run (EVAL_LIMIT is set), report not saved.\n\n{report}")
        return
    RESULTS_DIR.mkdir(exist_ok=True)
    model_slug = re.sub(r"[^A-Za-z0-9.]+", "-", llm.model)
    path = RESULTS_DIR / f"{date.today().isoformat()}_{model_slug}.md"
    path.write_text(report, encoding="utf-8", newline="\n")
    print(f"\nEval report written to {path}\n\n{report}")

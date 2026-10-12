<div align="center">

# FitFlow API

**An AI-powered workout and diet plan generator, where the LLM chooses and the code calculates.**

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-D71F00?style=for-the-badge&logo=sqlalchemy&logoColor=white)](https://www.sqlalchemy.org/)
[![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://www.docker.com/)

![Status](https://img.shields.io/badge/status-in%20development-orange?style=flat-square)
![Phase](https://img.shields.io/badge/phase-7%20of%208%20done-blue?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-blue?style=flat-square)

[About](#about) •
[Design Rules](#design-rules) •
[Quick Start](#quick-start) •
[Endpoints](#endpoints) •
[Roadmap](#roadmap)

</div>

---

> [!NOTE]
> **Work in progress.** Sign-up, login, user profiles, the catalogs, calorie and macro targets, and workout and diet plan generation and progress logging work today, and plan generation is measured by evals. CI and deployment are planned. See the [Roadmap](#roadmap).

## Table of Contents

- [About](#about)
- [Design Rules](#design-rules)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Quick Start](#quick-start)
- [Environment Variables](#environment-variables)
- [Endpoints](#endpoints)
- [Interactive Docs](#interactive-docs)
- [Running Tests](#running-tests)
- [Eval Results](#eval-results)
- [Roadmap](#roadmap)
- [License](#license)

---

## About

A user creates a profile: goal, age, sex, weight, height, activity level, training days per week, available equipment, allergies and dietary restrictions. **FitFlow** then generates a weekly workout plan and a daily meal plan, stores them, and can regenerate them later from logged progress.

| Feature            | Description                                                         | Status      |
| ------------------ | ------------------------------------------------------------------- | ----------- |
| **Authentication** | Sign up and log in with JWT tokens                                  | Available   |
| **Profile**        | The user's goal, body data, equipment, allergies and restrictions   | Available   |
| **Catalogs**       | Exercises and foods the plans are built from, with filters          | Available   |
| **Targets**        | Daily calories and macros, computed in code                         | Available   |
| **Workout plans**  | A weekly plan built from the exercise catalog                       | Available   |
| **Diet plans**     | A daily meal plan built from the food catalog                       | Available   |
| **Progress**       | Weight and workout logs that feed plan regeneration                 | Available   |

> FitFlow is a software project, not medical advice.

---

## Design Rules

The LLM is one component inside a pipeline the code controls.

1. **The LLM chooses, the code calculates.** Calorie and macro targets come from plain Python functions (Mifflin-St Jeor plus an activity multiplier) with unit tests. The model never does arithmetic.
2. **Catalog only.** The model may only use exercises and foods from FitFlow's own catalogs, referenced by ID.
3. **Structured output.** Model output is parsed into Pydantic models.
4. **Every plan is validated in code.** IDs exist, exercises match the user's equipment, no allergens or restricted foods, calories within 5% of target, protein at or above target. On failure the errors go back to the model, for at most 2 retries.
5. **One LLM wrapper.** Every model call goes through `app/core/llm.py`, with a timeout, retries, and logging of latency and token usage.
6. **Safety.** A minimum calorie floor, a cap on weekly weight change, no users under 18, and a "not medical advice" notice in every plan.

---

## Architecture

```mermaid
flowchart LR
    Client["Client<br/>(Web / Mobile)"] -->|HTTP + JWT| API
    subgraph App["FitFlow API (FastAPI)"]
        API["Routes"] --> Targets["Targets calculator<br/>pure Python"]
        API --> Wrapper["LLM wrapper"]
        API --> Validator["Plan validator"]
    end
    Wrapper -->|prompt + catalog| LLM["LLM provider"]
    LLM -->|structured plan| Wrapper
    API -->|SQLAlchemy async| DB[("PostgreSQL 16")]
```

---

## Tech Stack

| Category             | Technology                                   |
| -------------------- | -------------------------------------------- |
| Language & Framework | Python 3.11+, FastAPI                        |
| ASGI Server          | Uvicorn                                      |
| Database             | PostgreSQL 16                                |
| ORM & Driver         | SQLAlchemy 2.0 (async), asyncpg              |
| Migrations           | Alembic                                      |
| Authentication       | JWT (PyJWT), Argon2 password hashing         |
| Data Validation      | Pydantic, pydantic-settings                  |
| LLM                  | Local model served by Ollama, or hosted Claude (Anthropic SDK), behind one provider-agnostic wrapper |
| Testing              | Pytest, pytest-asyncio, HTTPX                |
| Documentation        | OpenAPI (Swagger UI & ReDoc)                 |
| Local Infrastructure | Docker Compose                               |

---

## Project Structure

<details>
<summary><b>Show file tree</b></summary>

```text
fitflow-api/
├── app/
│   ├── api/
│   │   ├── auth.py       # Sign up, login, current user
│   │   ├── deps.py       # Shared dependencies (database session, current user)
│   │   ├── diets.py      # Generate and read diet plans
│   │   ├── exercises.py  # Exercise catalog
│   │   ├── foods.py      # Food catalog
│   │   ├── profile.py    # Profile and calorie and macro targets
│   │   ├── progress.py   # Weight and workout logs
│   │   └── workouts.py   # Generate and read workout plans
│   ├── core/
│   │   ├── age.py        # Age calculation and the minimum age
│   │   ├── config.py     # Settings, read from .env
│   │   ├── database.py   # SQLAlchemy engine, sessions and Base
│   │   ├── diet_plans.py # Prompt, portion scaling and validation for diet plans
│   │   ├── enums.py      # Fixed choices (goal, equipment, allergens, ...)
│   │   ├── llm.py        # The one LLM wrapper: providers, tools, timeout, retries, logging
│   │   ├── plan_generation.py  # Generate, validate and retry loop for both plan types
│   │   ├── progress.py   # Progress summary, also added to plan prompts
│   │   ├── security.py   # Password hashing, password blocklist and JWT tokens
│   │   ├── targets.py    # Calorie and macro calculator (pure functions)
│   │   └── workout_plans.py  # Prompt and validation for workout plans
│   ├── data/
│   │   ├── common_passwords.txt  # Blocklist of common passwords
│   │   ├── exercises.json        # Exercise catalog data
│   │   └── foods.json            # Food catalog data
│   ├── models/           # SQLAlchemy models (tables)
│   ├── schemas/          # Pydantic schemas (requests and responses)
│   ├── main.py           # FastAPI entry point
│   └── seed.py           # Loads the catalogs into the database
├── alembic/              # Database migrations
├── tests/                # Automated tests (the LLM is mocked)
│   └── evals/            # Evals against the real model, with their results
├── alembic.ini           # Alembic configuration
├── docker-compose.yml    # Local PostgreSQL
├── pytest.ini            # Pytest configuration
├── requirements.txt      # Project dependencies
├── .env.example          # Environment variables template
└── .env                  # Environment variables (not committed)
```

</details>

---

## Quick Start

### Prerequisites

- [Python 3.11+](https://www.python.org/downloads/)
- [Git](https://git-scm.com/)
- [Docker Desktop](https://docs.docker.com/get-docker/), to run PostgreSQL
- [Ollama](https://ollama.com/download), to run the LLM locally (only needed to generate plans, not to run the tests)

### Installation

**1. Clone the repository**

```bash
git clone https://github.com/JoaoMeira29/fitflow-api.git
cd fitflow-api
```

**2. Create and activate a virtual environment**

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
```

**3. Install dependencies**

```bash
pip install -r requirements.txt
```

**4. Set up environment variables**

```bash
cp .env.example .env
```

Then fill in your own values (see [Environment Variables](#environment-variables)).

**5. Start the database, run migrations and load the catalogs**

```bash
docker compose up -d db
alembic upgrade head
python -m app.seed
```

The seed script loads the exercise and food catalogs. It is safe to run again.

**6. Download the LLM model**

```bash
ollama pull qwen2.5:7b
```

Ollama runs in the background after installation. To use another model, pull it and set `LLM_MODEL` in `.env`.

<details>
<summary><b>Using hosted Claude instead of a local model</b></summary>

The LLM wrapper has a second provider that calls Claude through the official Anthropic SDK. It costs money per request, so it is off by default. To switch, set these in `.env` and restart the server:

```
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=your-key-from-platform.claude.com
```

`LLM_MODEL` can stay empty (it defaults to `claude-opus-5-5`) or name another Claude model, such as `claude-haiku-5-5` for a much cheaper one. Nothing else changes: the same prompts, tools, validation, code repairs and retries run for both providers.

</details>

### The tool the model can call

When it builds a diet plan, the model can be given a **tool**: `calculate_nutrition`. It sends a list of foods and portions, and the code returns the calories and macros, so the model can check its plan with real arithmetic before answering. The answer is still scaled and validated in code afterwards.

The tool is off by default. To turn it on, set `LLM_USE_TOOLS=true` in `.env` and restart the server. The server log then shows how many times the model used it, as `tool_calls=3`.

It works with both providers, with a caveat for the local one. In a test of four plans, `qwen2.5:7b` called the tool in three of them and all four were valid, but each plan took 58 to 99 seconds, against about 19 seconds without the tool.

**7. Start the server**

```bash
uvicorn app.main:app --reload
```

The API will be available at <http://127.0.0.1:8000>. To check it is running:

```bash
curl http://127.0.0.1:8000/health
```

```json
{ "status": "healthy" }
```

### Try it

```bash
# Sign up
curl -X POST http://127.0.0.1:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "password": "a-long-password"}'

# Log in and copy the access_token from the response
curl -X POST http://127.0.0.1:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "password": "a-long-password"}'

# Call a protected route
curl http://127.0.0.1:8000/auth/me -H "Authorization: Bearer <access_token>"

# Save your profile
curl -X PUT http://127.0.0.1:8000/profile \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"goal": "lose_weight", "birth_date": "1995-06-15", "sex": "female",
       "height_cm": 165, "weight_kg": 68.5, "activity_level": "moderate",
       "training_days_per_week": 4, "equipment": ["bodyweight", "dumbbells"],
       "allergies": ["peanuts"], "dietary_restrictions": ["vegetarian"]}'

# Your calorie and macro targets
curl http://127.0.0.1:8000/profile/targets -H "Authorization: Bearer <access_token>"

# Browse the catalogs
curl "http://127.0.0.1:8000/exercises?muscle_group=chest&equipment=bodyweight" \
  -H "Authorization: Bearer <access_token>"
curl "http://127.0.0.1:8000/foods?diet=vegetarian&exclude_allergen=peanuts" \
  -H "Authorization: Bearer <access_token>"

# Generate a workout plan (a local model can take a minute)
curl -X POST http://127.0.0.1:8000/plans/workout \
  -H "Authorization: Bearer <access_token>"

# Generate a one-day meal plan
curl -X POST http://127.0.0.1:8000/plans/diet \
  -H "Authorization: Bearer <access_token>"

# Or choose the calories yourself (must be inside the safety limits)
curl -X POST http://127.0.0.1:8000/plans/diet \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"target_kcal": 1700}'

# Log today's weight, and a workout you completed
curl -X POST http://127.0.0.1:8000/progress/weight \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"weight_kg": 81.9}'
curl -X POST http://127.0.0.1:8000/progress/workouts \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"plan_day_id": "<id of a day in your workout plan>"}'

# See your progress over the last 30 days
curl http://127.0.0.1:8000/progress -H "Authorization: Bearer <access_token>"
```

Generating a plan again after logging gives a plan that knows your new weight and how many workouts you completed.

---

## Environment Variables

| Variable                      | Description                            | Example                                                       |
| ----------------------------- | -------------------------------------- | ------------------------------------------------------------- |
| `DATABASE_URL`                | PostgreSQL connection (asyncpg driver) | `postgresql+asyncpg://fitflow:fitflow@localhost:5432/fitflow` |
| `JWT_SECRET`                  | Secret key used to sign JWT tokens     | `a-long-random-string`                                        |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Access token lifetime, in minutes      | `30`                                                          |
| `MIN_DAILY_CALORIES_MALE`     | Lowest calorie target for men, in kcal (optional)   | `1500`                                           |
| `MIN_DAILY_CALORIES_FEMALE`   | Lowest calorie target for women, in kcal (optional) | `1200`                                           |
| `MAX_WEEKLY_WEIGHT_CHANGE_PERCENT` | Largest weekly weight change, as % of body weight (optional) | `1.0`                              |
| `LLM_PROVIDER`                | `ollama` (local) or `anthropic` (hosted Claude) (optional) | `ollama`                                  |
| `LLM_MODEL`                   | Model used to generate plans; empty means the provider's default (optional) | `qwen2.5:7b`             |
| `LLM_USE_TOOLS`               | Let the model call the nutrition calculator tool (optional) | `false`                                  |
| `LLM_BASE_URL`                | Address of the Ollama server (optional) | `http://localhost:11434`                                     |
| `ANTHROPIC_API_KEY`           | API key, only needed when `LLM_PROVIDER` is `anthropic` | `sk-ant-...`                                 |
| `LLM_TIMEOUT_SECONDS`         | Seconds to wait for one answer (optional) | `180`                                                      |
| `LLM_MAX_RETRIES`             | Extra attempts when the LLM is unreachable (optional) | `2`                                            |

To generate a secure `JWT_SECRET`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

> [!WARNING]
> The `.env` file contains credentials and **must not** be committed to the repository.

---

## Endpoints

| Method | Route               | Description                        | Auth | Status                                                                     |
| :----: | ------------------- | ---------------------------------- | :--: | -------------------------------------------------------------------------- |
| `GET`  | `/`                 | Welcome message                    | No   | ![](https://img.shields.io/badge/-available-brightgreen?style=flat-square) |
| `GET`  | `/health`           | API health check                   | No   | ![](https://img.shields.io/badge/-available-brightgreen?style=flat-square) |
| `POST` | `/auth/register`    | Create an account                  | No   | ![](https://img.shields.io/badge/-available-brightgreen?style=flat-square) |
| `POST` | `/auth/login`       | Get an access token                | No   | ![](https://img.shields.io/badge/-available-brightgreen?style=flat-square) |
| `GET`  | `/auth/me`          | The logged-in user                 | Yes  | ![](https://img.shields.io/badge/-available-brightgreen?style=flat-square) |
| `PUT`  | `/profile`          | Create or update the profile       | Yes  | ![](https://img.shields.io/badge/-available-brightgreen?style=flat-square) |
| `GET`  | `/profile`          | The logged-in user's profile       | Yes  | ![](https://img.shields.io/badge/-available-brightgreen?style=flat-square) |
| `GET`  | `/profile/targets`  | Computed calorie and macro targets | Yes  | ![](https://img.shields.io/badge/-available-brightgreen?style=flat-square) |
| `GET`  | `/exercises`        | Exercise catalog, with filters     | Yes  | ![](https://img.shields.io/badge/-available-brightgreen?style=flat-square) |
| `GET`  | `/foods`            | Food catalog, with filters         | Yes  | ![](https://img.shields.io/badge/-available-brightgreen?style=flat-square) |
| `POST` | `/plans/workout`    | Generate a weekly workout plan     | Yes  | ![](https://img.shields.io/badge/-available-brightgreen?style=flat-square) |
| `GET`  | `/plans/workout`    | List your workout plans            | Yes  | ![](https://img.shields.io/badge/-available-brightgreen?style=flat-square) |
| `GET`  | `/plans/workout/{id}` | One workout plan in full       | Yes  | ![](https://img.shields.io/badge/-available-brightgreen?style=flat-square) |
| `POST` | `/plans/diet`       | Generate a daily meal plan         | Yes  | ![](https://img.shields.io/badge/-available-brightgreen?style=flat-square) |
| `GET`  | `/plans/diet`       | List your diet plans               | Yes  | ![](https://img.shields.io/badge/-available-brightgreen?style=flat-square) |
| `GET`  | `/plans/diet/{id}`  | One diet plan in full              | Yes  | ![](https://img.shields.io/badge/-available-brightgreen?style=flat-square) |
| `POST` | `/progress/weight`  | Log your weight for a day          | Yes  | ![](https://img.shields.io/badge/-available-brightgreen?style=flat-square) |
| `POST` | `/progress/workouts` | Log a completed workout day       | Yes  | ![](https://img.shields.io/badge/-available-brightgreen?style=flat-square) |
| `GET`  | `/progress`         | Your logs and a summary            | Yes  | ![](https://img.shields.io/badge/-available-brightgreen?style=flat-square) |

The full list of planned routes is in the [roadmap](ROADMAP.html). The routes available right now are always in the [interactive docs](#interactive-docs).

---

## Interactive Docs

With the server running, FastAPI generates the documentation automatically:

| Interface      | URL                           |
| -------------- | ----------------------------- |
| **Swagger UI** | <http://127.0.0.1:8000/docs>  |
| **ReDoc**      | <http://127.0.0.1:8000/redoc> |

To call protected routes from Swagger UI, log in, click **Authorize** and paste the `access_token`.

---

## Running Tests

The tests need PostgreSQL running. They use a separate `fitflow_test` database, created automatically, so they never touch your development data.

```bash
docker compose up -d db
pytest
```

These tests mock the LLM, so they need no model and run in seconds.

### Evals

The evals generate plans with the **real model** for 30 varied profiles and report how often each validation rule passes. They are slow and need Ollama running, so plain `pytest` skips them. They do not use the database.

```bash
pytest -m evals -s
```

Each run writes a Markdown report to `tests/evals/results/`, named after the date and the model. To try another model, change `LLM_MODEL` in `.env` and run them again. The run fails if fewer than 90% of profiles end with a valid plan.

---

## Eval Results

Latest run: `qwen2.5:7b` on a local GPU, 30 profiles, 12 October 2026, with tools off. The full reports are in [tests/evals/results/](tests/evals/results/).

| Result                                   | Workout plans | Diet plans  |
| ---------------------------------------- | :-----------: | :---------: |
| Valid on the first attempt               | 28/30 (93%)   | 25/30 (83%) |
| Valid after retries                      | 2/30 (7%)     | 2/30 (7%)   |
| **Valid plan produced**                  | **30/30 (100%)** | **27/30 (90%)** |
| Failed after 2 retries                   | 0             | 3/30 (10%)  |
| Median time per plan                     | 23 s          | 19 s        |

Both evals meet the 90% threshold, the diet one only just.

How the model's first diet answer did, rule by rule, at each stage of the pipeline:

| Rule                                 | As the model wrote it | After calorie scaling | After protein repair |
| ------------------------------------ | :-------------------: | :-------------------: | :------------------: |
| Schema, four meals, known foods      | 30/30                 | -                     | -                    |
| No allergens, restrictions respected | 30/30                 | -                     | -                    |
| Calories within 5% of target         | 9/30 (30%)            | 30/30 (100%)          | 30/30 (100%)         |
| Protein at or above target           | 12/30 (40%)           | 16/30 (53%)           | 25/30 (83%)          |
| Every portion between 5 g and 600 g  | -                     | 30/30 (100%)          | 29/30 (97%)          |

What the numbers show:

- **The structural rules never failed.** The model is only offered items the user can use, and the JSON schema only allows those IDs, so it has no way to pick an allergen or an unknown item.
- **The model cannot do the arithmetic.** Left alone it hit the calorie target 30% of the time. Scaling in code makes that 100%, which is design rule 1 measured.
- **Protein is the one rule that still fails.** Code repair lifts the pass rate from 53% to 83% and retries recover two more plans. The three that still fail chose too few high-protein foods for the code to fix.

### What the evals changed

The first run produced a valid diet plan for 26 of 30 profiles (87%), below the threshold. All four failures were people losing weight whose protein target, at 2.0 g per kg of body weight, came to between 31% and 48% of their calories. That is a problem with the target, not the model, so protein is now capped at 35% of the day's calories. The run above is the one after that change; the earlier report is kept next to it.

One run each is a small sample: results move by a plan or two between runs, so the difference between 87% and 90% is suggestive, not proof.

---

## Roadmap

The full plan (design rules, how a plan is generated, the data model and every planned endpoint) is in **[ROADMAP.html](ROADMAP.html)** (open it in a browser).

| Phase | Goal                                              | Status                                                                  |
| :---: | ------------------------------------------------- | ----------------------------------------------------------------------- |
| 1     | Foundation and auth (settings, database, JWT)     | ![](https://img.shields.io/badge/-done-brightgreen?style=flat-square)   |
| 2     | User profile                                      | ![](https://img.shields.io/badge/-done-brightgreen?style=flat-square)   |
| 3     | Exercise and food catalogs, targets calculator    | ![](https://img.shields.io/badge/-done-brightgreen?style=flat-square)   |
| 4     | LLM wrapper and workout plan generation           | ![](https://img.shields.io/badge/-done-brightgreen?style=flat-square)   |
| 5     | Diet plan generation                              | ![](https://img.shields.io/badge/-done-brightgreen?style=flat-square)   |
| 6     | Evals (pass rate per validation rule)             | ![](https://img.shields.io/badge/-done-brightgreen?style=flat-square)   |
| 7     | Progress logging and plan regeneration            | ![](https://img.shields.io/badge/-done-brightgreen?style=flat-square)   |
| 8     | CI, Docker image, Kubernetes manifests, final docs | ![](https://img.shields.io/badge/-planned-lightgrey?style=flat-square) |

---

## License

Distributed under the **MIT** license.

<div align="center">

Built by [João Meira](https://github.com/JoaoMeira29)

</div>

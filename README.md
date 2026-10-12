<div align="center">

# FitFlow API

**An AI-powered workout and diet plan generator, where the LLM chooses and the code calculates.**

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-D71F00?style=for-the-badge&logo=sqlalchemy&logoColor=white)](https://www.sqlalchemy.org/)
[![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://www.docker.com/)

![Status](https://img.shields.io/badge/status-in%20development-orange?style=flat-square)
![Phase](https://img.shields.io/badge/phase-2%20of%208%20done-blue?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-blue?style=flat-square)

[About](#about) •
[Design Rules](#design-rules) •
[Quick Start](#quick-start) •
[Endpoints](#endpoints) •
[Roadmap](#roadmap)

</div>

---

> [!NOTE]
> **Work in progress.** Sign-up, login, JWT authentication and user profiles work today. Catalogs, plan generation and evals are planned. See the [Roadmap](#roadmap).

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
- [Roadmap](#roadmap)
- [License](#license)

---

## About

A user creates a profile: goal, age, sex, weight, height, activity level, training days per week, available equipment, allergies and dietary restrictions. **FitFlow** then generates a weekly workout plan and a daily meal plan, stores them, and can regenerate them later from logged progress.

| Feature            | Description                                                         | Status      |
| ------------------ | ------------------------------------------------------------------- | ----------- |
| **Authentication** | Sign up and log in with JWT tokens                                  | Available   |
| **Profile**        | The user's goal, body data, equipment, allergies and restrictions   | Available   |
| **Targets**        | Daily calories and macros, computed in code                         | Planned     |
| **Workout plans**  | A weekly plan built from the exercise catalog                       | Planned     |
| **Diet plans**     | A daily meal plan built from the food catalog                       | Planned     |
| **Progress**       | Weight and workout logs that feed plan regeneration                 | Planned     |

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
| LLM                  | Provider-agnostic wrapper (provider not chosen yet) |
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
│   │   └── profile.py    # Create, update and read the profile
│   ├── core/
│   │   ├── age.py        # Age calculation and the minimum age
│   │   ├── config.py     # Settings, read from .env
│   │   ├── database.py   # SQLAlchemy engine, sessions and Base
│   │   ├── enums.py      # Fixed choices (goal, equipment, allergens, ...)
│   │   └── security.py   # Password hashing, password blocklist and JWT tokens
│   ├── data/
│   │   └── common_passwords.txt  # Blocklist of common passwords
│   ├── models/           # SQLAlchemy models (tables)
│   ├── schemas/          # Pydantic schemas (requests and responses)
│   └── main.py           # FastAPI entry point
├── alembic/              # Database migrations
├── tests/                # Automated tests
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

**5. Start the database and run migrations**

```bash
docker compose up -d db
alembic upgrade head
```

**6. Start the server**

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
```

---

## Environment Variables

| Variable                      | Description                            | Example                                                       |
| ----------------------------- | -------------------------------------- | ------------------------------------------------------------- |
| `DATABASE_URL`                | PostgreSQL connection (asyncpg driver) | `postgresql+asyncpg://fitflow:fitflow@localhost:5432/fitflow` |
| `JWT_SECRET`                  | Secret key used to sign JWT tokens     | `a-long-random-string`                                        |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Access token lifetime, in minutes      | `30`                                                          |

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
| `GET`  | `/profile/targets`  | Computed calorie and macro targets | Yes  | ![](https://img.shields.io/badge/-planned-lightgrey?style=flat-square)     |
| `POST` | `/plans/workout`    | Generate a weekly workout plan     | Yes  | ![](https://img.shields.io/badge/-planned-lightgrey?style=flat-square)     |
| `POST` | `/plans/diet`       | Generate a daily meal plan         | Yes  | ![](https://img.shields.io/badge/-planned-lightgrey?style=flat-square)     |

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

---

## Roadmap

The full plan (design rules, how a plan is generated, the data model and every planned endpoint) is in **[ROADMAP.html](ROADMAP.html)** (open it in a browser).

| Phase | Goal                                              | Status                                                                  |
| :---: | ------------------------------------------------- | ----------------------------------------------------------------------- |
| 1     | Foundation and auth (settings, database, JWT)     | ![](https://img.shields.io/badge/-done-brightgreen?style=flat-square)   |
| 2     | User profile                                      | ![](https://img.shields.io/badge/-done-brightgreen?style=flat-square)   |
| 3     | Exercise and food catalogs, targets calculator    | ![](https://img.shields.io/badge/-planned-lightgrey?style=flat-square)  |
| 4     | LLM wrapper and workout plan generation           | ![](https://img.shields.io/badge/-planned-lightgrey?style=flat-square)  |
| 5     | Diet plan generation                              | ![](https://img.shields.io/badge/-planned-lightgrey?style=flat-square)  |
| 6     | Evals (pass rate per validation rule)             | ![](https://img.shields.io/badge/-planned-lightgrey?style=flat-square)  |
| 7     | Progress logging and plan regeneration            | ![](https://img.shields.io/badge/-planned-lightgrey?style=flat-square)  |
| 8     | CI, Docker image, Kubernetes manifests, final docs | ![](https://img.shields.io/badge/-planned-lightgrey?style=flat-square) |

---

## License

Distributed under the **MIT** license.

<div align="center">

Built by [João Meira](https://github.com/JoaoMeira29)

</div>

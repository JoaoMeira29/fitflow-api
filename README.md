<div align="center">

# FitFlow API

**A RESTful API for tracking and managing workouts, exercises and fitness plans.**

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-D71F00?style=for-the-badge&logo=sqlalchemy&logoColor=white)](https://www.sqlalchemy.org/)
[![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://www.docker.com/)
[![Kubernetes](https://img.shields.io/badge/Kubernetes-326CE5?style=for-the-badge&logo=kubernetes&logoColor=white)](https://kubernetes.io/)

![Status](https://img.shields.io/badge/status-in%20development-orange?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-blue?style=flat-square)

[About](#about) •
[Quick Start](#quick-start) •
[Endpoints](#endpoints) •
[Docker](#running-with-docker) •
[Roadmap](#roadmap)

</div>

---

> [!NOTE]
> **Work in progress.** The project structure and the FastAPI app are in place; authentication, workouts and exercises are being implemented. See the [Roadmap](#roadmap) for what is already available.

## Table of Contents

- [About](#about)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Quick Start](#quick-start)
- [Environment Variables](#environment-variables)
- [Endpoints](#endpoints)
- [Interactive Docs](#interactive-docs)
- [Running with Docker](#running-with-docker)
- [Deploying to Kubernetes](#deploying-to-kubernetes)
- [Running Tests](#running-tests)
- [Roadmap](#roadmap)
- [License](#license)

---

## About

**FitFlow** is a fitness tracking application. This API is its backend, and lets users:

| Feature            | Description                                  |
| ------------------ | -------------------------------------------- |
| **Authentication** | Sign up and log in with JWT tokens           |
| **Exercises**      | Browse a catalog of exercises                |
| **Workouts**       | Create, edit and log workouts                |
| **Progress**       | Track their progress over time               |

---

## Architecture

```mermaid
flowchart LR
    Client["Client<br/>(Web / Mobile)"] -->|HTTP / JSON + JWT| API["FitFlow API<br/>FastAPI + Uvicorn"]
    API -->|SQLAlchemy async| DB[("PostgreSQL")]
    Alembic["Alembic<br/>(migrations)"] -.->|schema| DB
```

---

## Tech Stack

| Category                        | Technology                               |
| ------------------------------- | ---------------------------------------- |
| Language & Framework            | Python 3.11+, FastAPI                    |
| ASGI Server                     | Uvicorn                                  |
| Database                        | PostgreSQL 16                            |
| ORM & Driver                    | SQLAlchemy 2.0 (async), asyncpg          |
| Migrations                      | Alembic                                  |
| Authentication                  | JWT (PyJWT), Argon2 password hashing     |
| Data Validation                 | Pydantic, pydantic-settings              |
| Testing                         | Pytest & HTTPX                           |
| Documentation                   | OpenAPI (Swagger UI & ReDoc)             |
| Containers & Orchestration      | Docker, Docker Compose, Kubernetes (k8s) |

---

## Project Structure

<details>
<summary><b>Show file tree</b></summary>

```text
fitflow-api/
├── app/
│   ├── api/              # API endpoints / routes
│   │   ├── auth.py       #   Sign up, login and session
│   │   ├── exercises.py  #   Exercise catalog
│   │   └── workouts.py   #   Workout management
│   ├── core/
│   │   ├── config.py     # Global settings (read from .env)
│   │   ├── database.py   # SQLAlchemy engine and sessions
│   │   └── security.py   # Password hashing and JWT tokens
│   ├── models/           # SQLAlchemy models (tables)
│   ├── schemas/          # Data validation schemas (Pydantic)
│   ├── main.py           # FastAPI entry point
│   └── seed.py           # Seeds the initial exercise catalog
├── alembic/              # Database migrations
├── tests/                # Automated tests with Pytest
├── k8s/                  # Kubernetes manifests (Deployment and Service)
├── Dockerfile            # Application Docker image
├── docker-compose.yml    # Local services orchestration
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
- [Docker & Docker Compose](https://docs.docker.com/get-docker/) (to run PostgreSQL), or a local [PostgreSQL 16](https://www.postgresql.org/download/) install

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
docker-compose up -d db
alembic upgrade head
```

**6. (Optional) Seed the exercise catalog**

```bash
python -m app.seed
```

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

---

## Environment Variables

| Variable                      | Description                               | Example                                                       |
| ----------------------------- | ----------------------------------------- | ------------------------------------------------------------- |
| `DATABASE_URL`                | PostgreSQL connection (asyncpg driver)    | `postgresql+asyncpg://fitflow:fitflow@localhost:5432/fitflow` |
| `JWT_SECRET`                  | Secret key used to sign JWT tokens        | `a-long-random-string`                                        |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Access token lifetime, in minutes         | `30`                                                          |

```env
DATABASE_URL=postgresql+asyncpg://fitflow:fitflow@localhost:5432/fitflow
JWT_SECRET=a-long-random-string
ACCESS_TOKEN_EXPIRE_MINUTES=30
```

To generate a secure `JWT_SECRET`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

> [!WARNING]
> The `.env` file contains credentials and **must not** be committed to the repository.

---

## Endpoints

| Method | Route        | Description           | Status                                                                      |
| :----: | ------------ | --------------------- | --------------------------------------------------------------------------- |
| `GET`  | `/`          | Welcome message       | ![](https://img.shields.io/badge/-available-brightgreen?style=flat-square)  |
| `GET`  | `/health`    | API health check      | ![](https://img.shields.io/badge/-available-brightgreen?style=flat-square)  |
|   —    | `/auth`      | Sign up, login, session | ![](https://img.shields.io/badge/-in%20progress-orange?style=flat-square) |
|   —    | `/exercises` | Exercise catalog      | ![](https://img.shields.io/badge/-in%20progress-orange?style=flat-square)   |
|   —    | `/workouts`  | Workout management    | ![](https://img.shields.io/badge/-in%20progress-orange?style=flat-square)   |

The full, always up-to-date list is in the [interactive docs](#interactive-docs).

---

## Interactive Docs

With the server running, FastAPI generates the documentation automatically:

| Interface      | URL                           |
| -------------- | ----------------------------- |
| **Swagger UI** | <http://127.0.0.1:8000/docs>  |
| **ReDoc**      | <http://127.0.0.1:8000/redoc> |

---

## Running with Docker

With the `.env` file set up, start the API and PostgreSQL together:

```bash
docker-compose up --build
```

---

## Deploying to Kubernetes

The manifests (Deployment and Service) live in the `k8s/` folder:

```bash
kubectl apply -f k8s/
```

---

## Running Tests

```bash
pytest
```

---

## Roadmap

The full plan (how the technologies fit together, the lifecycle of a request, the data model and the planned endpoints) is in **[ROADMAP.html](ROADMAP.html)** (open it in a browser).

| Phase | Goal                                          | Status                                                                     |
| :---: | --------------------------------------------- | -------------------------------------------------------------------------- |
| 1     | Foundation (settings, PostgreSQL, Alembic)    | ![](https://img.shields.io/badge/-in%20progress-orange?style=flat-square)  |
| 2     | Authentication (sign up, login, JWT)          | ![](https://img.shields.io/badge/-planned-lightgrey?style=flat-square)     |
| 3     | Exercise catalog and seed                     | ![](https://img.shields.io/badge/-planned-lightgrey?style=flat-square)     |
| 4     | Workout management (CRUD)                     | ![](https://img.shields.io/badge/-planned-lightgrey?style=flat-square)     |
| 5     | Progress and statistics                       | ![](https://img.shields.io/badge/-planned-lightgrey?style=flat-square)     |
| 6     | Quality (tests and CI)                        | ![](https://img.shields.io/badge/-planned-lightgrey?style=flat-square)     |
| 7     | Containers and deployment (Docker, Kubernetes)| ![](https://img.shields.io/badge/-planned-lightgrey?style=flat-square)     |

---

## License

Distributed under the **MIT** license.

<div align="center">

Built by [João Meira](https://github.com/JoaoMeira29)

</div>

<div align="center">

# FitFlow API

**API RESTful para acompanhamento e gestão de treinos, exercícios e planos de fitness.**

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-D71F00?style=for-the-badge&logo=sqlalchemy&logoColor=white)](https://www.sqlalchemy.org/)
[![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://www.docker.com/)
[![Kubernetes](https://img.shields.io/badge/Kubernetes-326CE5?style=for-the-badge&logo=kubernetes&logoColor=white)](https://kubernetes.io/)

![Estado](https://img.shields.io/badge/estado-em%20desenvolvimento-orange?style=flat-square)
![Licença](https://img.shields.io/badge/licen%C3%A7a-MIT-blue?style=flat-square)

[Sobre](#sobre-o-projeto) •
[Início Rápido](#início-rápido) •
[Endpoints](#endpoints) •
[Docker](#executar-com-docker) •
[Roadmap](#roadmap)

</div>

---

> [!NOTE]
> **Projeto em desenvolvimento.** A estrutura base e a aplicação FastAPI já estão criadas; os módulos de autenticação, treinos e exercícios estão a ser implementados. Consulta o [Roadmap](#roadmap) para ver o que já está disponível.

## Índice

- [Sobre o Projeto](#sobre-o-projeto)
- [Arquitetura](#arquitetura)
- [Tecnologias Utilizadas](#tecnologias-utilizadas)
- [Estrutura do Projeto](#estrutura-do-projeto)
- [Início Rápido](#início-rápido)
- [Variáveis de Ambiente](#variáveis-de-ambiente)
- [Endpoints](#endpoints)
- [Documentação Interativa](#documentação-interativa)
- [Executar com Docker](#executar-com-docker)
- [Deploy em Kubernetes](#deploy-em-kubernetes)
- [Executar Testes](#executar-testes)
- [Roadmap](#roadmap)
- [Licença](#licença)

---

## Sobre o Projeto

O **FitFlow** é uma aplicação de acompanhamento de fitness. Esta API é o backend que permite aos utilizadores:

| Funcionalidade        | Descrição                                               |
| --------------------- | ------------------------------------------------------- |
| **Autenticação**      | Criar conta e iniciar sessão com tokens JWT             |
| **Exercícios**        | Consultar um catálogo de exercícios                     |
| **Treinos**           | Criar, editar e registar treinos                        |
| **Progresso**         | Acompanhar a evolução ao longo do tempo                 |

---

## Arquitetura

```mermaid
flowchart LR
    Client["Cliente<br/>(Web / Mobile)"] -->|HTTP / JSON + JWT| API["FitFlow API<br/>FastAPI + Uvicorn"]
    API -->|SQLAlchemy async| DB[("PostgreSQL")]
    Alembic["Alembic<br/>(migrações)"] -.->|esquema| DB
```

---

## Tecnologias Utilizadas

| Categoria                      | Tecnologia                               |
| ------------------------------ | ---------------------------------------- |
| Linguagem & Framework          | Python 3.11+, FastAPI                    |
| Servidor ASGI                  | Uvicorn                                  |
| Base de Dados                  | PostgreSQL 16                            |
| ORM & Driver                   | SQLAlchemy 2.0 (async), asyncpg          |
| Migrações                      | Alembic                                  |
| Autenticação                   | JWT (PyJWT), hashing com Argon2          |
| Validação de Dados             | Pydantic, pydantic-settings              |
| Testes                         | Pytest & HTTPX                           |
| Documentação                   | OpenAPI (Swagger UI & ReDoc)             |
| Containerização & Orquestração | Docker, Docker Compose, Kubernetes (k8s) |

---

## Estrutura do Projeto

<details>
<summary><b>Ver árvore de ficheiros</b></summary>

```text
fitflow-api/
├── app/
│   ├── api/              # Endpoints / Rotas da API
│   │   ├── auth.py       #   Registo, login e sessão
│   │   ├── exercises.py  #   Catálogo de exercícios
│   │   └── workouts.py   #   Gestão de treinos
│   ├── core/
│   │   ├── config.py     # Configurações globais (lidas do .env)
│   │   ├── database.py   # Engine e sessões SQLAlchemy
│   │   └── security.py   # Hashing de passwords e tokens JWT
│   ├── models/           # Modelos SQLAlchemy (tabelas)
│   ├── schemas/          # Schemas de validação de dados (Pydantic)
│   ├── main.py           # Ponto de entrada do FastAPI
│   └── seed.py           # Script de povoamento inicial de exercícios
├── alembic/              # Migrações da base de dados
├── tests/                # Testes automatizados com Pytest
├── k8s/                  # Manifestos para Kubernetes (Deployment e Service)
├── Dockerfile            # Imagem Docker da aplicação
├── docker-compose.yml    # Orquestração local de serviços
├── requirements.txt      # Dependências do projeto
└── .env                  # Variáveis de ambiente (não versionado)
```

</details>

---

## Início Rápido

### Pré-requisitos

- [Python 3.11+](https://www.python.org/downloads/)
- [Git](https://git-scm.com/)
- [Docker & Docker Compose](https://docs.docker.com/get-docker/) (para correr o PostgreSQL), ou um [PostgreSQL 16](https://www.postgresql.org/download/) instalado localmente

### Instalação

**1. Clonar o repositório**

```bash
git clone https://github.com/JoaoMeira29/fitflow-api.git
cd fitflow-api
```

**2. Criar e ativar o ambiente virtual**

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
```

**3. Instalar as dependências**

```bash
pip install -r requirements.txt
```

**4. Configurar as variáveis de ambiente**

Cria um ficheiro `.env` na raiz do projeto (ver [Variáveis de Ambiente](#variáveis-de-ambiente)).

**5. Iniciar a base de dados e aplicar as migrações**

```bash
docker-compose up -d db
alembic upgrade head
```

**6. (Opcional) Povoar o catálogo de exercícios**

```bash
python -m app.seed
```

**7. Iniciar o servidor**

```bash
uvicorn app.main:app --reload
```

A API ficará disponível em <http://127.0.0.1:8000>. Para confirmar que está a funcionar:

```bash
curl http://127.0.0.1:8000/health
```

```json
{ "status": "healthy" }
```

---

## Variáveis de Ambiente

| Variável                      | Descrição                                   | Exemplo                                                        |
| ----------------------------- | ------------------------------------------- | -------------------------------------------------------------- |
| `DATABASE_URL`                | Ligação ao PostgreSQL (driver asyncpg)      | `postgresql+asyncpg://fitflow:fitflow@localhost:5432/fitflow`  |
| `JWT_SECRET`                  | Chave secreta para assinar os tokens JWT    | `uma-string-longa-e-aleatoria`                                 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Validade do token de acesso, em minutos     | `30`                                                           |

```env
DATABASE_URL=postgresql+asyncpg://fitflow:fitflow@localhost:5432/fitflow
JWT_SECRET=uma-string-longa-e-aleatoria
ACCESS_TOKEN_EXPIRE_MINUTES=30
```

Para gerar um `JWT_SECRET` seguro:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

> [!WARNING]
> O ficheiro `.env` contém credenciais e **não deve** ser incluído no repositório.

---

## Endpoints

| Método | Rota         | Descrição                    | Estado                                                            |
| :----: | ------------ | ---------------------------- | ----------------------------------------------------------------- |
| `GET`  | `/`          | Mensagem de boas-vindas      | ![](https://img.shields.io/badge/-disponível-brightgreen?style=flat-square) |
| `GET`  | `/health`    | Verificação do estado da API | ![](https://img.shields.io/badge/-disponível-brightgreen?style=flat-square) |
|   —    | `/auth`      | Registo, login e sessão      | ![](https://img.shields.io/badge/-em%20progresso-orange?style=flat-square)  |
|   —    | `/exercises` | Catálogo de exercícios       | ![](https://img.shields.io/badge/-em%20progresso-orange?style=flat-square)  |
|   —    | `/workouts`  | Gestão de treinos            | ![](https://img.shields.io/badge/-em%20progresso-orange?style=flat-square)  |

A lista completa e sempre atualizada está na [documentação interativa](#documentação-interativa).

---

## Documentação Interativa

Com o servidor em execução, a documentação é gerada automaticamente pelo FastAPI:

| Interface      | URL                                |
| -------------- | ---------------------------------- |
| **Swagger UI** | <http://127.0.0.1:8000/docs>       |
| **ReDoc**      | <http://127.0.0.1:8000/redoc>      |

---

## Executar com Docker

Com o ficheiro `.env` configurado, sobe a API e o PostgreSQL juntos:

```bash
docker-compose up --build
```

---

## Deploy em Kubernetes

Os manifestos (Deployment e Service) estão na pasta `k8s/`:

```bash
kubectl apply -f k8s/
```

---

## Executar Testes

```bash
pytest
```

---

## Roadmap

O plano completo — como as tecnologias interagem, o ciclo de um pedido, o modelo de dados e os endpoints planeados — está em **[ROADMAP.html](ROADMAP.html)** (abre no browser).

| Fase | Objetivo                                         | Estado                                                                        |
| :--: | ------------------------------------------------ | ----------------------------------------------------------------------------- |
| 1    | Fundação (configuração, PostgreSQL, Alembic)     | ![](https://img.shields.io/badge/-em%20progresso-orange?style=flat-square)    |
| 2    | Autenticação (registo, login, JWT)               | ![](https://img.shields.io/badge/-planeado-lightgrey?style=flat-square)       |
| 3    | Catálogo de exercícios e seed                    | ![](https://img.shields.io/badge/-planeado-lightgrey?style=flat-square)       |
| 4    | Gestão de treinos (CRUD)                         | ![](https://img.shields.io/badge/-planeado-lightgrey?style=flat-square)       |
| 5    | Progresso e estatísticas                         | ![](https://img.shields.io/badge/-planeado-lightgrey?style=flat-square)       |
| 6    | Qualidade (testes e CI)                          | ![](https://img.shields.io/badge/-planeado-lightgrey?style=flat-square)       |
| 7    | Containerização e deploy (Docker, Kubernetes)    | ![](https://img.shields.io/badge/-planeado-lightgrey?style=flat-square)       |

---

## Licença

Distribuído sob a licença **MIT**.

<div align="center">

Desenvolvido por [João Meira](https://github.com/JoaoMeira29)

</div>

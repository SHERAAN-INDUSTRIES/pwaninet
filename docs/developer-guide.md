# vasto Developer Guide

This guide takes a new developer from a clean clone to a fully running local development environment. It covers prerequisites, setup, architecture, common tasks, and troubleshooting.

---

## Table of contents

1. [Technology stack](#1-technology-stack)
2. [Prerequisites](#2-prerequisites)
3. [Clone and setup](#3-clone-and-setup)
4. [Environment configuration](#4-environment-configuration)
5. [Starting services](#5-starting-services)
6. [Database setup](#6-database-setup)
7. [Running the site](#7-running-the-site)
8. [Background workers](#8-background-workers)
9. [Static assets and media](#9-static-assets-and-media)
10. [Pwanimate setup](#10-pwanimate-setup)
11. [Android wrapper](#11-android-wrapper)
12. [Testing](#12-testing)
13. [Common commands](#13-common-commands)
14. [Architecture overview](#14-architecture-overview)
15. [Troubleshooting](#15-troubleshooting)
16. [Before opening a change](#16-before-opening-a-change)

---

## 1. Technology stack

| Layer | Technology | Purpose |
|---|---|---|
| **Language** | Python 3.11+ | Application runtime |
| **Framework** | Django 5.2 | Web framework (ORM, routing, templates, auth, admin) |
| **ASGI server** | Daphne 4.2 | HTTP + WebSocket serving (used in production Dockerfile) |
| **WSGI server** | Gunicorn (installed) | Alternative WSGI serving (not currently in Dockerfile) |
| **Database** | PostgreSQL 15+ with pgvector 0.3.6 | Primary data store; vector similarity search for Pwanimate |
| **Cache** | Redis 7 | Django cache, Channels layer, Celery broker |
| **Task queue** | Celery 5.3 | Background tasks (document ingestion, embeddings, reconciliation) |
| **API** | Django REST Framework 3.14 | JSON API layer |
| **API docs** | drf-spectacular | OpenAPI 3 schema generation, Swagger UI, ReDoc |
| **Frontend** | Django templates, HTMX, Bootstrap, vanilla JS, CSS | Server-rendered UI with progressive enhancement |
| **AI/LLM** | Custom AI gateway | Multi-provider LLM calls (Gemini, Groq, OpenRouter) |
| **Embeddings** | pgvector VectorField(768) | Vector storage; providers: Gemini, Jina, Voyage, Cloudflare |
| **Search** | PostgreSQL FTS + trigram | Full-text search across people, documents, posts, groups |
| **Mobile** | Capacitor 8.5 | Android native wrapper |
| **Storage** | Local filesystem / Cloudflare R2 | User uploads; S3-compatible object storage |
| **Auth** | Django auth + django-axes + pyotp | Session auth, brute-force lockout, 2FA TOTP |
| **Push** | Web Push (VAPID), FCM, APNS | Browser and mobile push notifications |
| **Localization** | Django i18n | English and Kiswahili |

---

## 2. Prerequisites

For platform-specific installation instructions for Ubuntu, Fedora, macOS, and Windows, see the [OS setup section in the README](../README.md#install-prerequisites-for-your-operating-system). The bootstrap scripts run in Bash on Linux and macOS. Native Windows setup is not supported or verified: there is no PowerShell bootstrap, and Vasto's script uses Bash, Linux-style Python environment paths, and Unix file-permission and key-file operations. On Windows, use Ubuntu in WSL 2 and run the commands inside its terminal; PowerShell and Command Prompt cannot run these scripts.

For the complete Vasto + Workspaces integration, use Python 3.12–3.14. Vasto by itself supports Python 3.11 or newer. Docker with Compose v2 must be running for Vasto's PostgreSQL and Redis containers. Workspaces uses a separate SQLite database by default.

Check the tools from the terminal you will use for setup:

```bash
python3 --version
git --version
docker --version
docker compose version
```

## 3. Clone and setup

### Clone the repository

Use the repository URL supplied by your team and open a terminal in the cloned Vasto folder:

```bash
git clone <repository-url>
cd vasto
```

For Windows, clone the repositories inside the Ubuntu WSL home directory (for example, `~/projects`) and run commands in the Ubuntu terminal. Do not run the Bash scripts from PowerShell or clone into `/mnt/c` for this workflow.

### Run the automated setup

From the Vasto repository root, run:

```bash
./scripts/setup-dev.sh
```

The script reuses an activated or existing compatible Python environment and creates `.venv` only when it cannot find one. It then installs Python dependencies, starts PostgreSQL and Redis, prepares and validates the local OIDC signing key, runs Django checks and migrations, selects an active staff owner (or creates a superuser if requested), then registers the local Workspaces client and saves its credentials outside the repository. It does not reset the database or start the long-running web server. If several nonstandard Python environments are found, activate the one you want and rerun setup.

Follow the prompts for a developer identifier and client owner. For non-interactive setup, supply values directly:

```bash
./scripts/setup-dev.sh --developer YOUR_IDENTIFIER --owner-username YOUR_VASTO_ADMIN_USERNAME
```

Use `--skip-containers` only when PostgreSQL and Redis are already running and configured. For the full Workspaces sign-in setup, open the Workspaces repository and follow its README after starting Vasto.

## 4. Environment configuration

The first setup creates `.env` from `.env.example` and generates a local Django `SECRET_KEY`. Existing `.env` files are retained. The database and cache settings are set up for the checked-in Compose services. Optional AI provider keys can be added to `.env` if you plan to use those features; basic local setup does not require them.

<<<<<<< HEAD
Keep `.env` private and do not commit it. Do not copy production secrets or another developer's OIDC signing key into your local environment. The signing key is stored outside the repository under `~/.config/vasto/keys/`.

## 5. Starting services

The bootstrap starts PostgreSQL and Redis for you. To confirm they are running, from the Vasto repository root use:
=======
```bash
cp .env.example .env
```

### Edit `.env`

Open `.env` in your editor. The file contains placeholder values for local development. Key variables:

```bash
# Database
DB_NAME=vasto
DB_USER=vasto
DB_PASSWORD=local_password
DB_HOST=127.0.0.1
DB_PORT=5432

# Redis
REDIS_URL=redis://127.0.0.1:6379/1
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/0

# Django
SECRET_KEY=<generate-a-fresh-random-string>
DEBUG=True

# Optional: AI providers (leave blank to use mock providers)
GEMINI_API_KEY=
GROQ_API_KEY=
OPENROUTER_API_KEY=

# Optional: Web search providers
TAVILY_API_KEY=
BRAVE_API_KEY=
SERPER_API_KEY=

# Optional: Cloudflare R2 storage
USE_S3=0
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET_NAME=
R2_ENDPOINT_URL=
R2_CUSTOM_DOMAIN=

# Optional: Push notifications
VAPID_PUBLIC_KEY=
VAPID_PRIVATE_KEY=
FIREBASE_CREDENTIALS_PATH=

# Optional: Email
EMAIL_HOST=
EMAIL_PORT=
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
```

### Generating a SECRET_KEY

```bash
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

### Important

- **Never commit `.env`** — it is listed in `.gitignore`.
- **Never reuse a production SECRET_KEY** in local development.
- **Leave AI provider keys blank** if you don't have them. Pwanimate will use mock providers.
- The local email backend prints emails to the console; no SMTP server needed for basic development.

---

## 5. Starting services

### Start PostgreSQL and Redis

```bash
docker compose up -d db redis
```

This starts:
- **PostgreSQL** with pgvector extension on host port 5432 (container port 5432)
- **Redis** on port 6379

The checked-in Compose file publishes PostgreSQL on host port 5432 by default, forwarding to PostgreSQL's container port 5432. Set `DB_PORT` in `.env` to use a different host port; Compose uses that same value for its host-side port while keeping the container port at 5432. Use `DB_HOST=127.0.0.1` for local development to avoid `localhost` resolving to IPv6 when PostgreSQL only accepts IPv4.

### Verify services are running
>>>>>>> 0d2ec7e59454798cbb6af0122c761d0d288afe66

```bash
docker compose ps
```

To stop the supporting containers when you finish, run:

```bash
docker compose down
```

Do not add `-v` unless you intend to delete the local database volume and its data. Docker commands work from Linux, macOS, or the Ubuntu WSL terminal after Docker is installed and running.

## 6. Database setup

The bootstrap checks database access, runs Django's system check, applies migrations, and prompts you to select or create a Vasto admin owner. You do not need to run separate migration or superuser commands during first-time setup.

Optional sample data can be created through the Django admin after starting Vasto. For direct Django shell access, run this from the repository root:

```bash
.venv/bin/python manage.py shell --settings=pwaninet.settings.local
```

## 7. Running the site

### Start the development server

The command below assumes the default `.venv`. If setup reused an environment with another name or location, copy the exact server command printed when setup finished.

```bash
.venv/bin/python manage.py runserver 127.0.0.1:8000 --settings=pwaninet.settings.local
```

The site is now available at:

| URL | Purpose |
|---|---|
| `http://127.0.0.1:8000/` | Home page (posts feed) |
| `http://127.0.0.1:8000/admin/` | Django admin panel |
| `http://127.0.0.1:8000/pwanimate/` | Pwanimate AI assistant |
| `http://127.0.0.1:8000/api/docs/` | API Swagger documentation |
| `http://127.0.0.1:8000/api/redoc/` | API ReDoc documentation |
| `http://127.0.0.1:8000/about/` | About vasto |
| `http://127.0.0.1:8000/privacy/` | Privacy Policy |
| `http://127.0.0.1:8000/licenses/` | Licences and attributions |
| `http://127.0.0.1:8000/pwanimate-about/` | About Pwanimate |
| `http://127.0.0.1:8000/pwanimate-about/` | About Pwanimate |
| `http://127.0.0.1:8000/search/` | Unified search |
| `http://127.0.0.1:8000/groups/` | Groups listing |
| `http://127.0.0.1:8000/documents/` | Document repository |
| `http://127.0.0.1:8000/notifications/` | Notifications |
| `http://127.0.0.1:8000/dashboard/` | Admin dashboard |

### Using Daphne (ASGI server)

For WebSocket support (group messaging, real-time features), use Daphne instead of `runserver`:

```bash
.venv/bin/python -m daphne -b 127.0.0.1 -p 8000 pwaninet.asgi:application --settings=pwaninet.settings.local
```

---

## 8. Background workers

Pwanimate and other features rely on Celery for background task processing.

### Start a Celery worker

In a separate Bash terminal. Windows developers should use the Ubuntu WSL terminal, just as they do for setup:

```bash
DJANGO_SETTINGS_MODULE=pwaninet.settings.local .venv/bin/celery -A pwaninet worker -l info
```

### Start Celery Beat (scheduled tasks)

In another terminal:

```bash
.venv/bin/celery -A pwaninet beat -l info
```

Celery Beat runs scheduled jobs such as document ingestion reconciliation (every 10 minutes).

### Task queues

Tasks are routed to named queues configured in `pwaninet/settings/base.py`. If a task appears not to run, check the queue configuration and ensure a worker is consuming from that queue.

---

## 9. Static assets and media

### Source assets

Edit source CSS, JavaScript, fonts, and interface images under `static/`. These files are version-controlled.

### Collect static (for production builds)

```bash
.venv/bin/python manage.py collectstatic --noinput --settings=pwaninet.settings.local
```

This gathers all static files into `staticfiles/`. **Do not edit or commit `staticfiles/`** — it is generated.

### User uploads

- **Development:** Uploads go to `media/` (local filesystem, git-ignored).
- **Production:** Configure Cloudflare R2 with `USE_S3=1` and valid credentials.
- **Private documents:** Stored in `private_media/` with access control. Never commit this directory.

---

## 10. Pwanimate setup

### How Pwanimate works

Pwanimate is a RAG (retrieval-augmented generation) system. The pipeline is:

```
User message → Orchestrator → Intent detection → Tool routing (deterministic + LLM)
                                    ↓
                        Context engine (assembles document chunks + user context)
                                    ↓
                        AI Gateway (calls LLM provider)
                                    ↓
                        Response with citations
```

### AI providers

Pwanimate supports multiple LLM providers with automatic fallback:

| Provider | Role | Environment variable |
|---|---|---|
| Google Gemini | Primary LLM | `GEMINI_API_KEY` |
| Groq | Fallback LLM | `GROQ_API_KEY` |
| OpenRouter | Fallback LLM | `OPENROUTER_API_KEY` |
| Mock | Development/testing | (no key needed) |

The fallback chain is: Gemini → Groq → OpenRouter → Mock.

### Embedding providers

Embeddings convert document chunks into vector representations for semantic search:

| Provider | Environment variable |
|---|---|
| Gemini Embedding | `GEMINI_API_KEY` |
| Jina | `JINA_API_KEY` |
| Voyage AI | `VOYAGE_API_KEY` |
| Cloudflare | `CLOUDFLARE_API_KEY` + `CLOUDFLARE_ACCOUNT_ID` |
| Mock | (no key needed) |

### Document ingestion

Documents uploaded to vasto are automatically processed:

1. **Extraction** — Text is extracted from PDF, DOCX, PPTX, or TXT files
2. **Chunking** — Content is split into 400-600 token chunks with structure awareness
3. **Embedding** — Chunks are converted to 768-dimensional vectors
4. **Storage** — Embeddings are stored in PostgreSQL via pgvector

This pipeline runs as Celery background tasks. You can monitor progress in the Celery worker logs.

### Backfill command

To re-ingest all existing documents (useful after schema changes):

```bash
.venv/bin/python manage.py backfill_pwanimate_chunks --settings=pwaninet.settings.local
```

### Web search

Pwanimate can search the public web using:

| Provider | Environment variable |
|---|---|
| Tavily | `TAVILY_API_KEY` |
| Brave Search | `BRAVE_API_KEY` |
| Serper | `SERPER_API_KEY` |

---

## 11. Android wrapper

The Capacitor configuration is in `capacitor.config.json`. The native Android project is in `android/`.

### Prerequisites

- Node.js 18+
- Android Studio (latest stable)
- Android SDK (API 33+)
- Java 17+

### Setup

```bash
npm install
npx cap sync android
npx cap open android
```

This opens the project in Android Studio for building and testing.

### Important

- Android SDK files, Gradle caches, build output, signing keys, and local properties must not be committed.
- The `android/` directory contains the native project wrapper, not the full Android Studio project metadata.

---

## 12. Testing

### Run the test suite

```bash
.venv/bin/python manage.py test --settings=pwaninet.settings.local
```

### Run tests for a specific app

```bash
.venv/bin/python manage.py test pwanimate --settings=pwaninet.settings.local
.venv/bin/python manage.py test users --settings=pwaninet.settings.local
.venv/bin/python manage.py test posts --settings=pwaninet.settings.local
```

### Run tests matching a pattern

```bash
.venv/bin/python manage.py test --settings=pwaninet.settings.local -k "test_name_pattern"
```

### System checks

```bash
.venv/bin/python manage.py check --settings=pwaninet.settings.local
```

### Test locations

| Directory | Purpose |
|---|---|
| `tests/legacy/` | Older standalone checks |
| `tests/manual/` | Manual debug pages |
| `tests/performance/` | Performance tests |
| `<app>/tests/` | Per-app test modules |

---

## 13. Common commands

### Django management

```bash
# System checks
.venv/bin/python manage.py check --settings=pwaninet.settings.local

# Make migrations (after model changes)
.venv/bin/python manage.py makemigrations --settings=pwaninet.settings.local

# Apply migrations
.venv/bin/python manage.py migrate --settings=pwaninet.settings.local

# Create superuser
.venv/bin/python manage.py createsuperuser --settings=pwaninet.settings.local

# Django shell
.venv/bin/python manage.py shell --settings=pwaninet.settings.local

# Collect static files
.venv/bin/python manage.py collectstatic --noinput --settings=pwaninet.settings.local

# Run tests
.venv/bin/python manage.py test --settings=pwaninet.settings.local
```

### Celery

```bash
# Start worker
.venv/bin/celery -A pwaninet worker -l info

# Start beat (scheduled tasks)
.venv/bin/celery -A pwaninet beat -l info

# Start worker + beat together (development)
.venv/bin/celery -A pwaninet worker -B -l info

# Monitor (requires Flower)
.venv/bin/celery -A pwaninet flower --port=5555
```

### Docker

```bash
# Start services
docker compose up -d db redis

# View logs
docker compose logs -f

# Stop services
docker compose down

# Stop and delete volumes (WARNING: deletes all data)
docker compose down -v

# Rebuild containers
docker compose up -d --build
```

---

## 14. Architecture overview

### Django apps

| App | Location | Purpose |
|---|---|---|
| `vasto` | `vasto/` | Project settings, root URLs, ASGI/WSGI, Celery config |
| `pwanimate` | `pwanimate/` | AI assistant (RAG, tools, orchestration) |
| `documents` | `documents/` | Academic document repository with academic hierarchy |
| `posts` | `posts/` | Social feed (text, images, video, reels) |
| `users` | `users/` | User accounts, profiles, authentication, 2FA |
| `groups` | `groups/` | Groups, membership, announcements, group messaging |
| `search` | `search/` | Unified cross-domain search |
| `notifications` | `notifications/` | Event-driven notification engine |
| `courses` | `courses/` | Legacy course catalogue |
| `releases` | `releases/` | Release management and "What's New" |
| `recommendations` | `recommendations/` | Recommendation engine |
| `realtime` | `realtime/` | WebSocket consumers |
| `admin_dashboard` | `admin_dashboard/` | Admin panel and feedback tickets |
| `messaging` | `messaging/` | Group messaging (direct messaging frozen) |

### Key patterns

- **Settings modules**: `pwaninet/settings/base.py` contains shared config; `local.py`, `production.py`, and `test.py` override per environment.
- **App structure**: Each Django app follows the standard layout with `models.py`, `views.py`, `urls.py`, `serializers.py` (for API apps), `services/`, `tasks/`, and `templates/`.
- **Authorization**: Privacy and visibility checks are enforced at the queryset level in services, not just in views.
- **Search**: PostgreSQL full-text search with `SearchVectorField` and trigram similarity fallback. Search vectors are updated via Django signals.
- **HTMX**: Many views support both full-page and partial (HTMX) responses. Check request headers for `HX-Request`.

---

## 15. Troubleshooting

### Database connection errors

- Ensure Docker containers are running: `docker compose ps`
- Check `DB_HOST` and `DB_PORT` in `.env`
- Verify PostgreSQL is accepting connections: `docker compose exec db psql -U vasto -d vasto`

### Redis connection errors

- Ensure Redis container is running: `docker compose ps`
- Check `REDIS_URL` in `.env`

### Migration errors

- If migrations fail, check for model inconsistencies: `.venv/bin/python manage.py makemigrations --check --dry-run --settings=pwaninet.settings.local`
- For pgvector issues, ensure the Docker image includes the extension

### Pwanimate not responding

- Check that Celery worker is running (for document ingestion)
- Check AI provider keys in `.env`
- Review Django logs for provider errors
- Without API keys, Pwanimate uses the mock provider

### Static files not loading

- Run `.venv/bin/python manage.py collectstatic --noinput --settings=pwaninet.settings.local`
- Check `STATIC_URL` and `STATIC_ROOT` in settings

### WebSocket not working

- Use Daphne instead of `runserver` for WebSocket support
- Ensure Redis is running (Channels layer uses Redis)
- Check `ASGI_APPLICATION` setting

### Celery tasks not running

- Ensure Celery worker is started: `.venv/bin/celery -A pwaninet worker -l info`
- Ensure Redis is running (broker)
- Check task routing configuration in `pwaninet/settings/base.py`
- Verify `DJANGO_SETTINGS_MODULE=pwaninet.settings.local` is set

---

## 16. Before opening a change

- **Keep changes within the responsible app** — avoid cross-app modifications unless necessary.
- **Update migrations** when models change. Run `makemigrations` and include migration files.
- **Update documentation** when behaviour or setup changes. Relevant docs: this guide, app-specific docs in `docs/apps/`, and student-facing pages in `templates/legal/`.
- **Never commit** local environment files, private uploads, logs, database dumps, generated static output, or signing keys.
- **Do not assume production state** — a feature being in the codebase or having environment variables does not mean it is active in production.
- **Test your changes** — run the test suite and system checks before submitting.
- **Read the site owner guide** ([site-owner-guide.md](site-owner-guide.md)) for end-to-end architecture understanding.

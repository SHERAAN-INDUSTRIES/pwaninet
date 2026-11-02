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

### Required

| Tool | Version | Purpose |
|---|---|---|
| Python | 3.11 or newer | Django application runtime |
| Git | Any recent version | Source control |
| Docker | 24.0+ with Compose plugin | PostgreSQL and Redis containers |

### Optional (for specific features)

| Tool | Purpose |
|---|---|
| Node.js 18+ | Capacitor/Android development, npm scripts |
| Android Studio + Android SDK | Building the Android app |
| A code editor | VS Code, PyCharm, or similar |

### Verifying prerequisites

```bash
python --version       # Should show 3.11+
docker --version       # Should show 24.0+
docker compose version  # Should show 2.0+
git --version
```

---

## 3. Clone and setup

### Clone the repository

```bash
git clone <repository-url>
cd vasto
```

### Create a virtual environment

```bash
python -m venv .venv
```

Activate it:

**Linux/macOS:**
```bash
source .venv/bin/activate
```

**Windows PowerShell:**
```powershell
.venv\Scripts\Activate.ps1
```

**Windows CMD:**
```cmd
.venv\Scripts\activate.bat
```

### Install Python dependencies

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

This installs Django, DRF, Celery, Channels, pgvector, PyMuPDF, python-docx, python-pptx, boto3, pyotp, and all other Python dependencies.

---

## 4. Environment configuration

### Copy the environment template

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

The checked-in Compose file publishes PostgreSQL on host port 5432. Set `DB_PORT` to the port published by your PostgreSQL service; the Compose mapping is currently fixed at `5432:5432`, so changing only `.env` does not change the Compose port. Use `DB_HOST=127.0.0.1` for local development to avoid `localhost` resolving to IPv6 when PostgreSQL only accepts IPv4.

### Verify services are running

```bash
docker compose ps
```

You should see both `db` and `redis` containers with status `running`.

### Stop services

```bash
docker compose down
```

**Warning:** Do not use `docker compose down -v` unless you intentionally want to delete the local database volume and all data.

---

## 6. Database setup

### Apply migrations

```bash
python manage.py migrate --settings=pwaninet.settings.local
```

This creates all database tables, including pgvector extension and search vector fields.

### Create a superuser

```bash
python manage.py createsuperuser --settings=pwaninet.settings.local
```

You will be prompted for username, email (optional), and password.

### Load sample data (optional)

There is no built-in sample data command. You can create test data through the Django admin at `/admin/` or by using the Django shell:

```bash
python manage.py shell --settings=pwaninet.settings.local
```

---

## 7. Running the site

### Start the development server

```bash
python manage.py runserver 127.0.0.1:8000 --settings=pwaninet.settings.local
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
python -m daphne -b 127.0.0.1 -p 8000 pwaninet.asgi:application --settings=pwaninet.settings.local
```

---

## 8. Background workers

Pwanimate and other features rely on Celery for background task processing.

### Start a Celery worker

In a separate terminal (with virtual environment activated):

**Linux/macOS:**
```bash
export DJANGO_SETTINGS_MODULE=pwaninet.settings.local
celery -A pwaninet worker -l info
```

**Windows PowerShell:**
```powershell
$env:DJANGO_SETTINGS_MODULE = "pwaninet.settings.local"
celery -A pwaninet worker -l info
```

### Start Celery Beat (scheduled tasks)

In another terminal:

```bash
celery -A pwaninet beat -l info
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
python manage.py collectstatic --noinput --settings=pwaninet.settings.local
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
python manage.py backfill_pwanimate_chunks --settings=pwaninet.settings.local
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
python manage.py test --settings=pwaninet.settings.local
```

### Run tests for a specific app

```bash
python manage.py test pwanimate --settings=pwaninet.settings.local
python manage.py test users --settings=pwaninet.settings.local
python manage.py test posts --settings=pwaninet.settings.local
```

### Run tests matching a pattern

```bash
python manage.py test --settings=pwaninet.settings.local -k "test_name_pattern"
```

### System checks

```bash
python manage.py check --settings=pwaninet.settings.local
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
python manage.py check --settings=pwaninet.settings.local

# Make migrations (after model changes)
python manage.py makemigrations --settings=pwaninet.settings.local

# Apply migrations
python manage.py migrate --settings=pwaninet.settings.local

# Create superuser
python manage.py createsuperuser --settings=pwaninet.settings.local

# Django shell
python manage.py shell --settings=pwaninet.settings.local

# Collect static files
python manage.py collectstatic --noinput --settings=pwaninet.settings.local

# Run tests
python manage.py test --settings=pwaninet.settings.local
```

### Celery

```bash
# Start worker
celery -A pwaninet worker -l info

# Start beat (scheduled tasks)
celery -A pwaninet beat -l info

# Start worker + beat together (development)
celery -A pwaninet worker -B -l info

# Monitor (requires Flower)
celery -A pwaninet flower --port=5555
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

- If migrations fail, check for model inconsistencies: `python manage.py makemigrations --check --dry-run --settings=pwaninet.settings.local`
- For pgvector issues, ensure the Docker image includes the extension

### Pwanimate not responding

- Check that Celery worker is running (for document ingestion)
- Check AI provider keys in `.env`
- Review Django logs for provider errors
- Without API keys, Pwanimate uses the mock provider

### Static files not loading

- Run `python manage.py collectstatic --noinput --settings=pwaninet.settings.local`
- Check `STATIC_URL` and `STATIC_ROOT` in settings

### WebSocket not working

- Use Daphne instead of `runserver` for WebSocket support
- Ensure Redis is running (Channels layer uses Redis)
- Check `ASGI_APPLICATION` setting

### Celery tasks not running

- Ensure Celery worker is started: `celery -A pwaninet worker -l info`
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

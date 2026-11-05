# vasto

vasto is a Django-based student community and learning-resource platform for students at Pwani University. It brings together student profiles and connections, a social feed, groups, direct messaging, an academic document repository, an AI assistant (Spark, formerly Pwanimate), notifications, unified search, recommendations, and a Progressive Web App (PWA) experience. An Android wrapper built with Capacitor is also included.

Vasto was formerly known as Pwaninet. The service domain (pwaninet.app), app identifiers, and technical references may still use the previous name; they refer to the same service.

## Table of contents

- [Features](#features)
- [Technology stack](#technology-stack)
- [Quick start](#quick-start)
- [Documentation](#documentation)
- [Project structure](#project-structure)
- [Repository rules](#repository-rules)

## Features

### Social feed (posts)

- Create text posts, image posts (with automatic thumbnail generation and WebP conversion), video posts (with HLS transcoding for mobile streaming), and link posts
- React to posts with likes, comment with replies and attachments, and repost to your followers or groups
- Control post visibility: public, authenticated users, followers only, or private
- Report content, hide posts from specific authors, and use custom stickers

### Groups

- Join open groups, request to join approval-based groups, or receive invitations to invite-only groups
- Group admins can pin announcements (normal, important, or urgent), share resources, and manage membership roles (admin, moderator, delegate, member)
- Each group has its own messaging with text, media, and system messages, plus photo likes
- Automatic group creation for courses, programmes, and academic levels

### Messaging

- Direct 1:1 and group conversations with real-time delivery over WebSockets
- Message attachments (images, files), reactions, link previews, and conversation themes
- Unread counts, media gallery per conversation, and user search for starting chats

### Academic documents

- Upload and browse past papers, lecture notes, slides, assignments, and other study materials across 13 categories
- Documents are organised by faculty, school, department, programme, and academic unit (course) with semester and year tracking
- Version history, multiple authors, tagging, and collections (personal or shared)
- Bookmark, rate, share, and request documents
- Moderation workflow with status tracking

### Spark (AI assistant, formerly Pwanimate)

- Custom RAG (retrieval-augmented generation) system with document chunking, pgvector embeddings, and hybrid semantic + lexical search
- 9 domain tools: academic lookup, document detail, group announcements, notification summary, people discovery, user profile, content search, web search, and local time
- Multi-provider AI gateway with fallback chain: Gemini, Groq, OpenRouter (mock provider for dev/testing)
- Embedding providers: Gemini, Jina, Voyage AI, Cloudflare (mock for dev/testing)
- Study Mode with progress tracking and checkpoints
- File attachment support (PDF, DOCX, PPTX, images, text)
- Web search via Tavily, Brave Search, or Serper
- Personalized preferences (tone, response style, personal instructions)
- See [About Spark](templates/legal/pwanimate.html) for full capability list

### People and profiles

- Extended user profiles with academic information (programme, academic level, semester, enrolled units), bio, headline, skills, projects, and social links
- Privacy controls: public, authenticated, followers-only, or private for each profile section
- Follow system with blocking, pinning, and profile photo likes
- Two-factor authentication (TOTP) with recovery codes
- Device session management

### Search and recommendations

- Unified cross-domain search (people, documents, posts, groups) using PostgreSQL full-text search with trigram fallback
- Personalized results: document search boosts your enrolled units; people search boosts verified and delegate roles
- Recommendation engine suggests classmates, groups, and documents based on your academic profile

### Notifications

- Event-driven notification engine with 18 notification types across social, academic, document, security, and system categories
- In-app, push (Web Push VAPID, Firebase FCM, Apple APNS), and email delivery
- Aggregation, quiet hours, per-type preferences, and actionable notifications

### PWA and mobile

- Progressive Web App with offline support, push notifications, and installable home screen
- Service worker with cache strategies for core assets, page shells, and offline media streaming (IndexedDB with HTTP range support)
- Capacitor Android wrapper with splash screen, status bar, and push notification plugins
- Release management system with version tracking and "What's New" screens

### Platform

- Admin dashboard with feedback ticketing and user activity tracking
- Localization (English and Kiswahili)
- Theme customization (light/dark/system, 8 chat themes, 16 font families, font size controls)
- Django REST Framework API with OpenAPI/Swagger documentation

## Technology stack

| Layer | Technology |
|---|---|
| Framework | Django 5.2 (Python 3.11+) |
| Database | PostgreSQL with pgvector 0.3.6 for vector embeddings |
| Task queue | Celery 5.3 with Redis broker and result backend |
| Real-time | Django Channels 4 with Daphne ASGI server |
| Cache | Redis (with local memory fallback) |
| API | Django REST Framework 3.14 with drf-spectacular (OpenAPI/Swagger) |
| Frontend | Django templates, HTMX, Bootstrap, vanilla JS, CSS |
| AI/LLM | Custom AI gateway with Gemini, Groq, OpenRouter providers |
| Embeddings | pgvector VectorField(768); Gemini, Jina, Voyage, Cloudflare providers |
| Search | PostgreSQL full-text search + trigram similarity |
| Mobile | Capacitor 8.5 (Android) |
| Storage | Local filesystem (default) or Cloudflare R2 via django-storages/boto3 |
| Push | Web Push (VAPID), Firebase Cloud Messaging, Apple Push Notification Service |
| Auth | Django auth + django-axes (brute-force lockout) + pyotp (2FA TOTP) |

## Quick start

### Prerequisites

- Linux or macOS. Windows 10/11 users should use Ubuntu on WSL 2.
- Git, OpenSSL, and Python 3.11 or newer for Vasto. For both Vasto and Workspaces, use Python 3.12–3.14.
- Docker with Docker Compose v2. Docker must be running for Vasto's PostgreSQL and Redis services.
- Node.js and Android Studio are only needed for mobile/Android development.

### Install prerequisites for your operating system

Run only the instructions for your computer. After installing these tools, the Vasto and Workspaces setup commands below are the same in a Bash terminal.

#### Ubuntu

Open Terminal and install the basic tools:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip openssl
```

Check `python3 --version`. For the complete Vasto + Workspaces setup, install Python 3.12, 3.13, or 3.14; Ubuntu 22.04 may have an older default Python. Install Docker Engine and the Compose plugin by following [Docker's Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/), then make sure your account can run Docker commands using [Docker's Linux post-install instructions](https://docs.docker.com/engine/install/linux-postinstall/).

#### Fedora

Open Terminal and install the basic tools:

```bash
sudo dnf install -y git python3 python3-pip openssl
```

Check `python3 --version`. For the complete Vasto + Workspaces setup, use Python 3.12, 3.13, or 3.14. Install Docker Engine and Compose by following [Docker's Fedora instructions](https://docs.docker.com/engine/install/fedora/), start the Docker service, and follow [Docker's Linux post-install instructions](https://docs.docker.com/engine/install/linux-postinstall/) so your account can run Docker commands.

#### macOS

Install Apple's command line tools if needed, then install Git and Python with [Homebrew](https://brew.sh/):

```bash
xcode-select --install
brew install git python@3.12 openssl
```

Install [Docker Desktop for Mac](https://docs.docker.com/desktop/setup/install/mac-install/), open it, and wait until Docker reports that it is running. Python 3.12 is supported for both repositories.

#### Windows 10/11

Native Windows setup is not currently supported or verified. There is no PowerShell bootstrap, and the Vasto script requires Bash, Linux-style Python environment paths, and Unix file-permission and key-file operations. Docker Desktop alone does not make that script runnable from PowerShell. Use Ubuntu in WSL 2 for the complete Vasto + Workspaces setup:

1. Open PowerShell as Administrator and install WSL with Ubuntu:

   ```powershell
   wsl --install -d Ubuntu
   ```

   Restart Windows if prompted, then open Ubuntu from the Start menu and create its Linux username and password. See [Microsoft's WSL installation guide](https://learn.microsoft.com/en-us/windows/wsl/install) if Windows asks for an update or the command is unavailable.
2. Install [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/). In Docker Desktop settings, enable the WSL 2 engine and WSL integration for Ubuntu. Follow [Docker's WSL setup guide](https://docs.docker.com/desktop/features/wsl/).
3. In the Ubuntu terminal (not PowerShell), install tools:

   ```bash
   sudo apt update
   sudo apt install -y git python3 python3-venv python3-pip openssl
   ```
4. Keep both repositories inside the WSL Linux home folder, such as `~/projects`, rather than under `/mnt/c`. Run all setup and server commands from the Ubuntu terminal. Check that Ubuntu has Python 3.12–3.14 and that `docker compose version` works before continuing.

### Set up Vasto step by step

You do not need to know Django or create a Python environment yourself. The script installs the Python packages and performs the Django setup for you.

1. Open a terminal in the Vasto repository's top-level folder. This is the folder containing this README and `scripts/`.
2. Start Docker Desktop or your Docker service, then run:

   ```bash
   ./scripts/setup-dev.sh
   ```

<<<<<<< HEAD
3. Follow the prompts. Choose a short developer identifier, such as your username. Select an existing Vasto staff account to own the local Workspaces client, or leave the owner prompt blank to create a Vasto superuser. If you create one, the script asks you to set its password. The owner is a Vasto account; you will use a Vasto account to sign in during testing.
4. Wait for the script to finish. It reuses an activated environment, or searches this repository for an existing Python environment named `.venv`, `venv`, or `env` (and a unique other environment). It creates `.venv` only if it cannot find one. It installs dependencies into the selected environment, prepares `.env`, starts the database and Redis, checks or creates the local signing key, applies migrations, and registers the Workspaces client. The client credentials are saved outside the repository with private file permissions.
5. Start the Vasto web server. The command below is for the default `.venv`; if the script selected another environment, copy the exact start command it printed at the end:
=======
   OAuth support is provided by the `django-oauth-toolkit` distribution (pinned
   in `requirements.txt`). Its Python import path is `oauth2_provider`; do not
   install a separate distribution named `oauth2_provider`. To verify the
   dependency after installation:

   ```bash
   python -m pip show django-oauth-toolkit
   python manage.py shell --settings=pwaninet.settings.local -c "from oauth2_provider.urls import metadata_urlpatterns; print(f'Loaded {len(metadata_urlpatterns)} OAuth metadata routes')"
   ```

   If `pip show oauth2_provider` reports a separately installed distribution,
   remove that conflicting package and reinstall the project dependencies. If
   the environment has been used for other projects, creating a fresh `.venv`
   is the safest option.

4. **Configure environment**
>>>>>>> 0d2ec7e59454798cbb6af0122c761d0d288afe66

   ```bash
   .venv/bin/python manage.py runserver 127.0.0.1:8000 --settings=pwaninet.settings.local
   ```

<<<<<<< HEAD
   Leave this terminal open while testing. Vasto is now available at `http://127.0.0.1:8000/` and its admin page is at `http://127.0.0.1:8000/admin/`.
=======
   Edit `.env` with your local values. Key variables:
   - `DB_NAME`, `DB_USER`, `DB_PASSWORD` — PostgreSQL credentials
   - `DB_HOST=127.0.0.1`, `DB_PORT=5432` — local connection to the PostgreSQL
     service started by the checked-in Compose file. Use the actual published
     port if you connect to a different PostgreSQL server. Use `127.0.0.1`
     rather than `localhost` to avoid connecting over IPv6 when PostgreSQL only
     accepts IPv4.
   - `REDIS_URL=redis://127.0.0.1:6379/1` — cache
   - `CELERY_BROKER_URL=redis://127.0.0.1:6379/0` — Celery broker
   - `SECRET_KEY` — generate a fresh Django secret key
   - AI provider keys (optional for basic functionality): `GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`
>>>>>>> 0d2ec7e59454798cbb6af0122c761d0d288afe66

For a repeatable setup where you already know the values, replace the prompts with:

```bash
./scripts/setup-dev.sh --developer YOUR_IDENTIFIER --owner-username YOUR_VASTO_ADMIN_USERNAME
```

If you have several nonstandard environments in the repository, activate the one you want before running setup. Setup stops with an instruction to activate the one you want and rerun the script. Use `--skip-containers` only if PostgreSQL and Redis are already running and configured for this Vasto checkout. The setup script does not delete or reset your database or replace an existing signing key.

### Set up Workspaces and test sign-in

To test the Vasto sign-in integration, keep Vasto running and open a second terminal.

1. Go to the top-level folder of the Workspaces repository (the folder containing its README and `scripts/`).
2. Run the Workspaces setup script, using the same identifier you chose for Vasto if you need to select the credentials file explicitly:

   ```bash
   ./scripts/setup-dev.sh --credentials-file ~/.config/vasto-workspaces/YOUR_IDENTIFIER-local.env
   ```

   This reuses an activated or existing compatible Python environment (creating `.venv` only if none is found), installs Workspaces dependencies, imports the client credentials without printing the secret, prepares its own database, applies migrations, and checks the running Vasto provider. If you are using one generated credentials file, you can run `./scripts/setup-dev.sh` with no options and let it find the file or prompt you.
3. When setup reports success, start Workspaces. This command uses the default `.venv`; if the script reused a differently named environment, use the exact server command it printed:

   ```bash
   .venv/bin/python workspace/manage.py runserver 127.0.0.1:8001
   ```

4. Open `http://127.0.0.1:8001/api/auth/vasto/start/` in a browser and sign in to Vasto. The first sign-in may ask you to approve the Workspaces access request.

You only need the setup scripts for environment, credentials, database migrations, registration, and provider checks. For a normal login test, start both servers and use the browser. The servers run in separate terminals; leave both terminals open while testing.

Start Celery only when you are developing background jobs. Open another terminal in Vasto and run:

```bash
export DJANGO_SETTINGS_MODULE=pwaninet.settings.local
celery -A pwaninet worker -l info
```

Celery Beat is optional for scheduled tasks:

```bash
celery -A pwaninet beat -l info
```

## Documentation

- [Documentation index](docs/README.md) — Overview of all documentation
- [Site overview](docs/site-overview.md) — Architecture, app map, data and files
- [Vasto–Workspaces OIDC provider guide](docs/integrations/vasto-workspaces-oidc-provider.md) — Vasto provider configuration, client registration, consent, keys, and local testing

React frontend setup and integration documentation are maintained separately by the frontend team.
- [Developer guide](docs/developer-guide.md) — Detailed setup and contribution guide
- [Site owner guide](docs/site-owner-guide.md) — End-to-end walkthrough of the platform
- [App-by-app guides](docs/apps/README.md) — Detailed guides for each Django app
- [Licensing and attributions](docs/licensing-and-attributions.md) — Third-party licence inventory
- [Pre-launch documentation](docs/pre-launch/) — Drafts and internal checklists (not student-facing)

### Student-facing pages

- [About vasto](templates/legal/about.html) — Platform overview
- [Privacy Policy](templates/legal/privacy.html) — Data handling practices
- [Licences and attributions](templates/legal/licenses.html) — Third-party notices
- [About Spark](templates/legal/pwanimate.html) — AI assistant capabilities

## Project structure

```
vasto/
├── manage.py                  # Django management entry point
├── requirements.txt           # Python dependencies
├── docker-compose.yml         # PostgreSQL + Redis for local dev
├── Dockerfile                 # Production image (Daphne + Gunicorn)
├── vasto/                  # Project configuration
│   ├── settings/              # base.py, local.py, production.py, test.py
│   ├── urls.py                # Root URL configuration
│   ├── asgi.py                # ASGI application (Daphne)
│   ├── wsgi.py                # WSGI application (Gunicorn)
│   └── celery.py              # Celery app configuration
├── pwanimate/                 # Spark AI assistant app (module name retained for compatibility)
│   ├── ai/                    # LLM gateway, providers, embeddings
│   ├── context/               # Context engine, user context, types
│   ├── ingestion/             # Document extractors and chunker
│   ├── retrieval/             # Document retrieval, unified search adapter
│   ├── orchestrator/          # Main orchestration service and prompts
│   ├── tools/                 # Deterministic tool system (9 domain tools)
│   ├── people/                # Peer discovery service
│   ├── services/              # Conversation, attachments, study sessions
│   ├── tasks/                 # Celery tasks (ingestion, embeddings, reconciliation)
│   ├── api/                   # REST API views and serializers
│   ├── templates/             # Spark UI templates
│   └── management/            # Management commands (backfill, etc.)
├── documents/                 # Academic document repository
│   ├── academic/              # Academic hierarchy (faculty → unit)
│   ├── api.py, serializers.py, urls.py
│   └── models.py, views.py
├── posts/                     # Social feed
├── users/                     # User accounts, profiles, auth
├── groups/                    # Groups and communities
├── search/                    # Unified cross-domain search
├── notifications/             # Event-driven notification engine
├── courses/                  # Legacy course catalogue
├── releases/                  # Release management
├── recommendations/           # Recommendation engine
├── realtime/                  # WebSocket consumers
├── admin_dashboard/           # Admin panel and feedback
├── messaging/                 # Direct and group messaging (conversations, attachments, reactions, themes)
├── templates/                 # Global Django templates
│   └── legal/                 # About, Privacy, Licenses, Spark pages
├── static/                    # Source frontend assets
├── android/                   # Capacitor Android project
├── docs/                      # Project documentation
├── scripts/                   # Operational scripts
└── tests/                     # Test suites
```

## Repository rules

- **Never commit secrets**: `.env` files, credentials, signing keys, database dumps, local uploads, or private documents must never be committed to Git.
- **Environment template**: `.env.example` is a placeholder. Put real local secrets in an ignored `.env` file.
- **Uploads are not in Git**: User-uploaded media belongs in configured object storage/CDN for production. Local `media/` is development storage and must remain out of Git.
- **Static assets**: Source static assets in `static/` are part of the application and are version-controlled. `staticfiles/` is generated by `collectstatic` and must remain out of Git.
- **No open-source licence**: Do not add an open-source licence for vasto without an explicit decision. Third-party assets keep their own licence terms.
- **Update documentation**: When changing behaviour or setup, update the relevant documentation.
- **Migrations**: Always include Django migrations when changing models.
- **Test before submitting**: Run the test suite and system checks before opening a change.

## Important notes

- The repository `docker-compose.yml` starts PostgreSQL and Redis only. Django, Celery workers, and Celey Beat must be started separately.
- The production Dockerfile starts Daphne (ASGI). Gunicorn is available as a WSGI option but not currently used in the Dockerfile.
- Spark uses pgvector for semantic search. The PostgreSQL Docker image includes the pgvector extension.
- AI features require provider API keys. Without keys, Spark falls back to a mock provider for development and testing.
- vasto is an independent project. It is not an official Pwani University service or assignment unless confirmed in writing.

For detailed setup instructions, see the [developer guide](docs/developer-guide.md).

## Local developer bootstrap for Workspaces

The [step-by-step setup above](#set-up-vasto-step-by-step) covers the complete local Vasto and Workspaces flow, including the commands to start both servers and test sign-in. The Vasto setup script registers one client per developer identifier and saves its credentials to `~/.config/vasto-workspaces/YOUR_IDENTIFIER-local.env`. The Workspaces setup script imports that file and runs the live provider check. Keep the client secret in the backend environment; never put it in React configuration.

## Internal API for Workspaces Integration

Vasto exposes internal REST API endpoints for Workspaces to fetch user data. These endpoints are protected by service token authentication and are intended for server-to-server communication only.

### Internal API Endpoints

All endpoints require `Authorization: Token <service_token>` header.

- `GET /users/api/internal/users/{id}/profile/` — User profile with academic info
- `GET /users/api/internal/users/{id}/documents/` — User's uploaded documents
- `GET /users/api/internal/users/{id}/institution/` — User's institution details
- `GET /users/api/internal/users/{id}/courses/` — User's enrolled courses
- `GET /users/api/internal/users/{id}/groups/` — User's group memberships
- `GET /users/api/internal/users/{id}/notifications/` — User's notifications
- `GET /users/api/internal/users/{id}/conversations/` — User's conversations

### Service Account Setup

The setup script automatically creates a service account named `workspaces_service` and generates a `VASTO_SERVICE_TOKEN`. This token is stored in `.env` and should be copied to the Workspaces `.env` file.

### Environment Variables

```bash
# Internal API Configuration
VASTO_SERVICE_TOKEN=<generated-token>
VASTO_INTERNAL_API_ENABLED=true
INTERNAL_API_ALLOWED_IPS=127.0.0.1
```

### Celery and Redis

Vasto uses Celery for background tasks and Redis for caching/message queuing. The setup script shows commands to start Celery worker, Beat scheduler, and Flower monitoring.

```bash
# Start Celery worker
.venv/bin/python manage.py celery -A pwaninet worker --loglevel=info --concurrency=2

# Start Celery Beat scheduler
.venv/bin/python manage.py celery -A pwaninet beat --loglevel=info

# Monitor with Flower
.venv/bin/python manage.py flower -A pwaninet --port=5555
```

### Redis Database Allocation

| Database | Purpose | Owner |
|----------|---------|-------|
| 0 | Celery broker/results | Pwaninet |
| 1 | Cache + Channels | Pwaninet |
| 2 | Django cache | Workspaces |
| 3 | Celery broker | Workspaces |
| 4 | Celery results | Workspaces |

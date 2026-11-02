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

- Python 3.11+
- Docker with Compose plugin
- Node.js (only for Capacitor/Android development)
- Android Studio + Android SDK (only for Android development)

### Setup

1. **Clone the repository**

   ```bash
   git clone <repository-url>
   cd vasto
   ```

2. **Create and activate a virtual environment**

   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Linux/macOS
   # or: .venv\Scripts\Activate.ps1  # Windows PowerShell
   ```

3. **Install dependencies**

   ```bash
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   ```

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

   ```bash
   cp .env.example .env
   ```

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

5. **Start PostgreSQL and Redis**

   ```bash
   docker compose up -d db redis
   ```

6. **Run migrations**

   ```bash
   python manage.py migrate --settings=pwaninet.settings.local
   ```

7. **Create an admin account**

   ```bash
   python manage.py createsuperuser --settings=pwaninet.settings.local
   ```

8. **Start the development server**

   ```bash
   python manage.py runserver 127.0.0.1:8000 --settings=pwaninet.settings.local
   ```

9. **Start Celery (in a separate terminal)**

   ```bash
   export DJANGO_SETTINGS_MODULE=pwaninet.settings.local
   celery -A pwaninet worker -l info
   ```

10. **Start Celery Beat (optional, for scheduled tasks)**

    ```bash
    celery -A pwaninet beat -l info
    ```

The site is now running at `http://127.0.0.1:8000/`. The admin panel is at `/admin/`. API documentation is at `/api/docs/`.

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

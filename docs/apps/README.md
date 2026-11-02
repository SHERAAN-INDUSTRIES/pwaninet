# vasto App Guides

Each guide describes a first-party Django app registered by the project. The code is the source of truth; routes and feature availability can change as the service evolves.

| App | Responsibility | Guide |
| --- | --- | --- |
| `users` | Accounts, profiles, authentication, privacy, and connections | [Users](users.md) |
| `posts` | Feed posts, media, comments, reactions, and reports | [Posts](posts.md) |
| `groups` | Campus groups, membership, invitations, and announcements | [Groups](groups.md) |
| `courses` | Legacy course catalogue and academic-unit relations | [Courses](courses.md) |
| `documents` | Academic classifications and study-resource repository | [Documents](documents.md) |
| `notifications` | Notification events, preferences, and push delivery | [Notifications](notifications.md) |
| `search` | Site-wide search views and suggestions | [Search](search.md) |
| `recommendations` | User, group, and document recommendation services | [Recommendations](recommendations.md) |
| `pwanimate` | AI assistant, conversation history, retrieval, and integrations | [Pwanimate](pwanimate.md) |
| `releases` | Release notes, version metadata, and update information | [Releases](releases.md) |
| `admin_dashboard` | Staff dashboard and user feedback | [Admin dashboard](admin-dashboard.md) |
| `messaging` | Direct messaging code, currently frozen behind gates | [Messaging](messaging.md) |
| `workspace` | Account sync entry point for the external vasto Workspace system | [Workspace](workspace.md) |

Project-wide settings, URL mounting, Channels, and Celery are documented in the [site overview](../site-overview.md) and [developer guide](../developer-guide.md).

# Development Guidelines

## Localization

**CRITICAL:** When adding new pages, UI components, or user-facing text:

1. **Add localization keys** to backend localizations table
2. **Support all 6 UI languages:** EN, TR, RU, ES, PT, DE
3. **Use translation helper** in frontend: `const { t } = useLocalization()`
4. **Never hardcode strings** — always use `t('key.path')`

### Backend (add to main.py)

Add entries to `_INITIAL_LOCALIZATIONS` list:

```python
_INITIAL_LOCALIZATIONS = [
    # ...
    ("your.new.key", "English text", "Türkçe metin", "Русский текст", "Texto español", "Texto português", "Deutscher Text"),
]
```

Keys are loaded into the database on startup and cached in memory. Changes require app restart.

### Frontend

Use the localization hook:

```tsx
const { t } = useLocalization()

// In JSX:
{t('your.new.key')}
```

### Examples

- Privacy Policy page → `privacy.title`, `privacy.section1.title`, `privacy.section1.content`
- Settings modal → `settings.preferences`, `settings.account`, `settings.subscription`
- Buttons → `button.save`, `button.cancel`, `button.continue`

### Nested Keys

Use dot notation for hierarchical organization:

```python
("settings.account.email", "Email", "E-posta", "Эл. почта", "Correo", "E-mail", "E-Mail"),
("settings.account.password", "Password", "Şifre", "Пароль", "Contraseña", "Senha", "Passwort"),
```

---

## Database Changes

All schema migrations must be **idempotent** and added to `database._migrate_playlists_pg()`:

```python
def _migrate_playlists_pg() -> None:
    """Add columns to playlists and songs on PostgreSQL (idempotent)."""
    statements = [
        "ALTER TABLE playlists ADD COLUMN IF NOT EXISTS is_hidden BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS preferred_lang VARCHAR(8) NOT NULL DEFAULT 'en'",
        # Add your migration here
    ]
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
```

### Best Practices

- Use `ADD COLUMN IF NOT EXISTS` for new columns
- Use `CREATE TABLE IF NOT EXISTS` for new tables
- Always provide `NOT NULL DEFAULT` values for new columns on existing tables
- Test locally before deploying
- Document breaking changes in commit messages

---

## Rate Limiting

Apply rate limits to new authentication or public endpoints using existing dependencies:

### Available Rate Limiters

```python
_rl_login           = rate_limit(5,   15 * 60, "login")           # 5 req/15min
_rl_register        = rate_limit(3,   15 * 60, "register")        # 3 req/15min
_rl_forgot_password = rate_limit(3,   15 * 60, "forgot_password") # 3 req/15min
_rl_reset_password  = rate_limit(5,   15 * 60, "reset_password")  # 5 req/15min
_rl_oauth           = rate_limit(10,  15 * 60, "oauth")           # 10 req/15min
_rl_webhook         = rate_limit(100, 60,      "webhook")         # 100 req/min
```

### Usage

Add as dependency to route:

```python
@app.post("/api/auth/new-endpoint", response_model=UserResponse)
def new_endpoint(
    body: NewEndpointRequest,
    db: Session = Depends(get_db),
    _rl: None = _rl_login,  # Reuse existing limiter or create new one
):
    # ... implementation
```

### Creating New Rate Limiters

```python
_rl_custom = rate_limit(
    limit=10,              # max requests
    window_seconds=60,     # per window (seconds)
    key_prefix="custom",   # unique prefix
    endpoint_name="custom" # for alerts
)
```

---

## Deploy Workflow

Standard deployment process:

```bash
# Stage changes
git add <files>
git commit -m "feat: descriptive commit message"

# Stash any uncommitted work
git stash

# Deploy (builds frontend, rsyncs to server, restarts backend)
bash deploy.sh

# Restore uncommitted work
git stash pop
```

### What deploy.sh Does

1. Builds frontend (`npm run build`)
2. Rsyncs `backend/`, `frontend/dist/`, `worker/` to server
3. Restarts backend service (`sudo systemctl restart flowup-backend`)
4. No automatic database migrations — migrations run on backend startup

### Rollback

```bash
git revert <commit-hash>
bash deploy.sh
```

---

## API Endpoints

### Rate Limited Endpoints

- `POST /api/auth/login` → 5 req/15min per IP
- `POST /api/auth/register` → 3 req/15min per IP
- `POST /api/auth/forgot-password` → 3 req/15min per IP
- `POST /api/auth/reset-password` → 5 req/15min per IP
- `POST /api/auth/google` → 10 req/15min per IP
- `POST /api/auth/apple` → 10 req/15min per IP
- `POST /api/mailgun/webhook` → 100 req/min per IP

### Admin Endpoints

Require `_require_admin` dependency (rate limited at 30 req/min per user ID).

---

## Email System

### Sending Emails

Use existing Mailgun helpers in `backend/mailgun.py`:

```python
import mailgun as _mailgun

# Welcome email (uses stored template)
_mailgun.send_welcome_email(to=email, display_name=name, t=translations)

# Password reset (uses stored template)
_mailgun.send_password_reset(to=email, display_name=name, reset_url=url, t=translations)

# Custom email
_mailgun._send(to=email, subject="Subject", text="Body", html="<p>Body</p>")
```

### Email Templates

Stored in Mailgun dashboard:
- "Welcome Email" — requires variables: `name`, `greeting`, `welcome_title`, `body_text`, `footer_text`
- "password reset email" — requires variables: `name`, `greeting`, `body_text`, `button_title`, `action_url`, `footer_text`

### Threading

Always send emails in background thread (fire-and-forget):

```python
def _send_async():
    _mailgun.send_welcome_email(to=email, display_name=name, t=t)

threading.Thread(target=_send_async, daemon=True).start()
```

---

## Analytics

### Frontend Tracking

Use `analytics.ts` wrapper:

```tsx
import { track } from './analytics'

// Track event
track('Song Started', { songId: song.id, title: song.title })
track('Word Inspected', { lemma: word.lemma })
```

### Event Naming

Use Title Case for event names. Existing events:
- `Sign Up`, `Login`
- `Song Started`, `Song Completed`
- `Word Inspected`
- `Page View` (automatic)

### Properties

Pass relevant context as properties object. Keep property names camelCase.

---

## Testing

### Rate Limit Testing

```bash
cd backend
python test_rate_limit.py
```

Hits forgot-password endpoint repeatedly to verify limits and alerts.

### Manual Testing

```bash
# Start backend
cd backend
uvicorn main:app --reload --port 8000

# Start frontend (separate terminal)
cd frontend
npm run dev
```

Visit `http://localhost:5173`

---

## Common Patterns

### Protected Routes

```python
@app.get("/api/protected")
def protected_endpoint(
    user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    # user is guaranteed to be authenticated
```

### Admin Routes

```python
@app.get("/api/admin/something")
def admin_endpoint(
    user: User = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    # user is guaranteed to be admin
```

### Translation Helper

```python
# In backend endpoint:
_t = _get_email_t(kind="welcome", lang=user.preferred_lang, db=db)
# Returns dict with all keys starting with "email.welcome."
# Use: _t.get("subject", "fallback")
```

---

## Environment Variables

Required in `~/.credentials` on server:

```bash
DATABASE_URL=postgresql://...
MAILGUN_API_KEY=...
MAILGUN_WEB_HOOK_KEY=...
ADMIN_PERSONAL_EMAIL=...
DEEPL_API_KEY=...
WORKER_API_KEY=...
JWT_SECRET=...
```

---

## File Structure

```
Flowup/
├── backend/
│   ├── main.py          # FastAPI app
│   ├── database.py      # SQLAlchemy models
│   ├── models.py        # Pydantic schemas
│   ├── mailgun.py       # Email helpers
│   ├── google_auth.py   # Google OAuth
│   ├── apple_auth.py    # Apple Sign In
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.tsx           # Main component
│   │   ├── analytics.ts      # Plausible wrapper
│   │   ├── api/client.ts     # Backend API
│   │   ├── i18n/             # Localization
│   │   └── components/
│   ├── package.json
│   └── vite.config.ts
├── pipeline/            # NLP data generation
├── worker/              # Whisper alignment
├── eval/                # Dictionary evaluation
├── planning/            # Weekly plans, daily reports
│   └── docs/            # Project documentation
├── deploy.sh
├── start.sh
└── DEVELOPMENT.md       # This file
```

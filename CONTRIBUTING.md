# Contributing to Webcast

Thanks for your interest in contributing. Webcast is a small internal tool but welcomes improvements, bug fixes, and documentation updates.

## Getting Started

```bash
git clone https://github.com/evanalexander-dev/webcast.git
cd webcast
cp .env.example .env
# Edit .env with your local configuration
pip install -r requirements.txt
cd backend && python main.py
```

The app runs on port 80 by default. Change `PORT` in `.env` or run with `uvicorn backend.main:app --port 8080` for local development without root.

## Project Structure

```
backend/          FastAPI application
  routers/        API endpoint handlers
  services/       Business logic (scheduler, YouTube API, FFmpeg, camera, email)
  static/         Frontend — vanilla JS, no build step required
docs/             GitHub Pages (homepage and privacy policy)
setup.sh          Raspberry Pi installation script
migrate_tokens.py Token migration utility from older app version
```

## Development Guidelines

**Backend**
- Keep routers thin — business logic belongs in services
- All database access goes through functions in `database.py`
- Use the module-level `logger = logging.getLogger(__name__)` pattern
- Prefer `async/await` throughout; avoid blocking calls in async context

**Frontend**
- Vanilla JS only — no framework, no build step
- Module-level state variables at the top of `app.js`
- CSS variables for all colors (defined in `:root` in `style.css`)
- Role visibility via `body.is-admin` and `body.is-specialist` CSS classes

**Database**
- Schema is defined once in `init_db()` with `CREATE TABLE IF NOT EXISTS`
- Migrations for existing installs use `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
- All DB functions use the `get_db()` context manager

## Submitting Changes

1. Fork the repository
2. Create a branch: `git checkout -b fix/description` or `feature/description`
3. Make your changes
4. Test on a Raspberry Pi or equivalent Linux system if possible
5. Open a pull request with a clear description of what changed and why

## Reporting Issues

Open a GitHub issue with:
- What you expected to happen
- What actually happened
- Relevant log output (`sudo journalctl -u webcast -n 50`)
- Your hardware and OS version

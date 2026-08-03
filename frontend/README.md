# AI Group Chat MVP

A group chat where humans and an AI share a persistent channel. Users join with a display name, see full history, and can @mention the AI to get streamed responses. Built with React (stream-chat-react), FastAPI, and Stream Chat.

## Features (MVP)

- **Single shared channel** — One persistent channel (`messaging:general` by default); all users see the same history.
- **Onboarding** — Enter a display name; the backend issues a Stream token and adds you to the channel.
- **Text messages & reactions** — Send messages and add emoji reactions (stream-chat-react).
- **AI on @mention** — Mention `@AI` or `@ai-assistant` in a message; after a 2.5s debounce the backend streams the AI reply into the channel (typewriter effect via message partial updates).
- **Message history** — New joiners load full channel history from Stream.

## Quick start

### 1. Backend (FastAPI)

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env        # fill in Stream + OpenAI keys
uvicorn main:app --reload --port 8000
```

**Backend env (`.env`):**

- `STREAM_API_KEY`, `STREAM_API_SECRET` — from your Stream dashboard.
- `STREAM_CHANNELS` — allowlist of joinable channel ids (default `dev2,general`); a group's join code is its channel id.
- `STREAM_CHANNEL_ID` — channel used when a link carries no `?c=` code (default `general`).
- `OPENAI_API_KEY` — for streaming completions (e.g. gpt-4o-mini).
- `AI_USER_ID`, `AI_USER_NAME` — Stream user for the bot (default `ai-assistant` / `AI Assistant`).

### 2. Frontend (React + Vite)

```bash
npm install
cp .env.example .env       # set VITE_STREAM_API_KEY and VITE_AI_ASSISTANT_URL
npm run dev
```

**Frontend env (`.env`):**

- `VITE_STREAM_API_KEY` — same as backend Stream API key.
- `VITE_AI_ASSISTANT_URL` — backend URL (e.g. `http://localhost:8000`).

The active channel is no longer a build-time env var — it comes from the join code in the URL (`?c=<code>`), so one build serves every group.

No `VITE_STREAM_USER_TOKEN` — the app gets the token from the backend after the user enters their name.

### 3. Use the app

1. Open your group's link (`<app-url>/?c=<join-code>`), enter a display name, click Join. Without a `?c=` code the form asks for one and falls back to the default channel if left blank.
2. You’re in your group's channel; send messages and add reactions.
3. To get an AI reply, include `@AI` or `@ai-assistant` in a message; the AI responds after a short debounce with a streamed message.

## Architecture (summary)

- **Auth:** Frontend calls `POST /token` with `user_name` and the `channel_id` join code from the URL; backend validates the code against `STREAM_CHANNELS`, upserts a channel-scoped Stream user, creates a JWT, adds them (and the AI user) to that channel, and returns the token plus the resolved `channel_id`. Frontend stores all of it in sessionStorage and uses the token with Stream’s JS client.
- **One channel per group:** The active channel is `messaging:<channel_id>` as resolved at join time. Following a different group's link in the same tab clears the stored session and re-onboards, rather than reusing the previous identity.
- **AI trigger:** When a user sends a message containing `@AI` or `@ai-assistant`, the frontend debounces 2.5s then calls `POST /start-ai-agent` with `channel_id` and `channel_type`. Backend runs in the background: fetches last 20 messages, creates an empty AI message in Stream, then streams the LLM response by repeatedly updating that message’s `text` (partial update), so the UI shows a typewriter effect.
- **Reactions:** Handled by stream-chat-react; no custom backend needed.

## API (backend)

- **`POST /token`**  
  Body: `{ "user_name": string, "channel_id": string | null }` (`channel_id` = the group's join code; omit for the default channel)  
  Response: `{ "token": string, "user_id": string, "user_name": string, "channel_id": string }`  
  Validates the code against `STREAM_CHANNELS` (404 if unknown), upserts a channel-scoped user, adds them + the AI user to that channel, returns a Stream JWT.

- **`POST /start-ai-agent`**  
  Body: `{ "channel_id": string, "channel_type": string }` (default type `messaging`)  
  Response: `{ "status": "started" }`  
  Starts the AI agent in the background for that channel (fetches history, streams reply into the channel).

## Project structure

- `src/` — React app: onboarding, `ChatContent` (channel list + channel), `Composer` (input + @mention trigger), `MessageBubble`, etc.
- `backend/` — FastAPI app: `/token`, `/start-ai-agent`, Stream SDK, OpenAI streaming, partial message updates.

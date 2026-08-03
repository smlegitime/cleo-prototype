"""
Authenticated corpus fetch for the `generate` lifecycle step.

Takes the queries derived in `queries.py`, fetches real posts via the authenticated
`searchPosts` endpoint, and normalizes them into subjects the labeler-engine interpreter can
evaluate. This is how rule quality is measured against real posts for *any* labeler — the
dev2 disability-advocacy spec is just one of many, so nothing here is hard-coded to a domain.

Design boundaries (see the extensibility-seam and sandbox-executor notes):
  * All I/O lives here (the enrichment layer). The interpreter stays pure, and so does the
    query derivation next door in `queries.py`.
  * The corpus should be cached by the caller under `corpus_key(spec)` — a fingerprint of the
    query basis, NOT the spec_id — so editing rules replays the SAME posts and quality diffs
    stay attributable to the rules rather than to a different sample.

Auth uses ONE service-level, read-only app password (BSKY_HANDLE / BSKY_APP_PASSWORD) shared
across all labelers. It is the application's fetch credential and has nothing to do with a
labeler's own identity, which is minted separately at the `provision` step.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import TypedDict

import httpx

from src.agent.spec import LabelerSpec
from src.agent.lifecycle.queries import QueryBucket, corpus_key, derive_queries

logger = logging.getLogger(__name__)

# createSession runs against the entryway (the account's home service); authenticated
# searchPosts runs against the AppView. public.api.bsky.app is unauthenticated-only and 403s
# on search, which is why we hit api.bsky.app with a bearer token instead.
ENTRYWAY = "https://bsky.social"
APPVIEW = "https://api.bsky.app"

# Restrict the rule-quality corpus to one language via searchPosts' `lang` filter. Defaults to
# English: the labeler has NO language signal (a word fires the same in any language), so keeping the
# test corpus English-only keeps cross-language false positives (e.g. French "en retard" = "late")
# out of the quality report. Env-overridable; set CORPUS_LANG="" to disable the filter entirely.
CORPUS_LANG = os.environ.get("CORPUS_LANG", "en")


# Author metadata for account-trait signals.
class AccountTraits(TypedDict, total=False):
    account_age_days: int | None
    follower_count: int | None
    following_count: int | None
    post_count: int | None
    has_avatar: bool
    has_description: bool


class CorpusPost(TypedDict):
    uri: str | None
    cid: str | None
    handle: str | None
    display_name: str | None
    text: str
    created_at: str | None
    langs: list[str] | None
    media: bool             # has an image embed
    alt_text: str | None    # joined image alt text, for future image/alt signals
    account: AccountTraits | None  # author metadata for account-trait signals; None until enriched
    query: str              # the query that surfaced this post
    bucket: QueryBucket


# --- Fetch — authenticated I/O

def authenticate(handle: str, app_password: str, *, client: httpx.Client) -> dict:
    """Create a session against the entryway. Returns the createSession payload (accessJwt, ...).

    Request: POST {ENTRYWAY}/xrpc/com.atproto.server.createSession
    Body: {"identifier": <handle>, "password": <app_password>}

    The identifier must be a bare handle/DID/email — a leading "@" makes createSession 400, so
    we strip it defensively since users naturally write handles with the @.
    """
    resp = client.post(
        f"{ENTRYWAY}/xrpc/com.atproto.server.createSession",
        json={"identifier": handle.strip(), "password": app_password},
    )
    resp.raise_for_status()
    return resp.json()


def search_posts(
    q: str,
    *,
    access_jwt: str,
    client: httpx.Client,
    limit: int = 25,
    cursor: str | None = None,
    lang: str | None = CORPUS_LANG,
) -> dict:
    """One authenticated searchPosts call against the AppView. Returns the raw response JSON.

    `lang` restricts results to that post language (defaults to CORPUS_LANG, English); pass a falsy
    value to fetch posts in any language.
    """
    params: dict = {"q": q, "limit": limit}
    if cursor:
        params["cursor"] = cursor
    if lang:
        params["lang"] = lang
    resp = client.get(
        f"{APPVIEW}/xrpc/app.bsky.feed.searchPosts",
        params=params,
        headers={"Authorization": f"Bearer {access_jwt}"},
    )
    resp.raise_for_status()
    return resp.json()


# --- account-trait enrichment (only runs when the spec actually uses account signals) ---

_GETPROFILES_BATCH = 25  # app.bsky.actor.getProfiles caps at 25 actors per call


def spec_uses_account_signals(spec: LabelerSpec) -> bool:
    """True if any rule in the spec carries an `account` signal (include or exclude).

    Guards the enrichment I/O: keyword/pattern-only labelers pay nothing for author profiles.
    """
    for label in spec.get("labels") or []:
        rule = label.get("rule")
        if not rule:
            continue
        for group in rule.get("include_groups") or []:
            if any(s.get("type") == "account" for s in group.get("all_of") or []):
                return True
        if any(s.get("type") == "account" for s in rule.get("exclude_signals") or []):
            return True
    return False


def get_profiles(actors: list[str], *, access_jwt: str, client: httpx.Client) -> list[dict]:
    """One authenticated getProfiles call (<=25 actors). Returns the raw `profiles` list."""
    resp = client.get(
        f"{APPVIEW}/xrpc/app.bsky.actor.getProfiles",
        params=[("actors", a) for a in actors],
        headers={"Authorization": f"Bearer {access_jwt}"},
    )
    resp.raise_for_status()
    return resp.json().get("profiles") or []


def _account_traits(profile: dict) -> AccountTraits:
    """Derive the six enforceable account traits from a getProfiles profile view."""
    created = profile.get("createdAt")
    age_days: int | None = None
    if created:
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - dt).days
        except ValueError:
            age_days = None
    return {
        "account_age_days": age_days,
        "follower_count": profile.get("followersCount"),
        "following_count": profile.get("followsCount"),
        "post_count": profile.get("postsCount"),
        "has_avatar": bool(profile.get("avatar")),
        "has_description": bool((profile.get("description") or "").strip()),
    }


def enrich_accounts(posts: list[CorpusPost], *, access_jwt: str, client: httpx.Client) -> None:
    """Populate each post's `account` block in place via batched getProfiles calls.

    Best-effort: a failed batch is logged and skipped, leaving those posts' `account` as None (which
    the interpreter treats as "trait unknown -> predicate does not fire"). Keyed by handle, which is
    what `_normalize` captured; unique handles are batched 25 at a time.
    """
    handles = list({p["handle"] for p in posts if p.get("handle")})
    if not handles:
        return
    traits_by_handle: dict[str, AccountTraits] = {}
    for i in range(0, len(handles), _GETPROFILES_BATCH):
        batch = handles[i:i + _GETPROFILES_BATCH]
        try:
            profiles = get_profiles(batch, access_jwt=access_jwt, client=client)
        except Exception:
            logger.exception("getProfiles failed for a batch; leaving those accounts unenriched")
            continue
        for prof in profiles:
            h = prof.get("handle")
            if h:
                traits_by_handle[h] = _account_traits(prof)
    for p in posts:
        p["account"] = traits_by_handle.get(p.get("handle"))


def _normalize(post: dict, query: str, bucket: QueryBucket) -> CorpusPost:
    author = post.get("author") or {}
    record = post.get("record") or {}
    embed = post.get("embed") or {}
    images = embed.get("images") or [] if isinstance(embed, dict) and "images" in (embed.get("$type") or "") else []
    alts = [i.get("alt", "") for i in images if isinstance(i, dict) and i.get("alt")]
    return {
        "uri": post.get("uri"),
        "cid": post.get("cid"),
        "handle": author.get("handle"),
        "display_name": author.get("displayName"),
        "text": record.get("text") or "",
        "created_at": record.get("createdAt"),
        "langs": record.get("langs"),
        "media": bool(images),
        "alt_text": " ".join(alts) or None,
        "account": None,  # filled in by _enrich_accounts when the spec uses account signals
        "query": query,
        "bucket": bucket,
    }


def fetch_corpus(
    spec: LabelerSpec,
    *,
    per_query: int = 25,
    max_posts: int = 200,
    timeout: float = 20.0,
) -> list[CorpusPost]:
    """Derive queries from the spec, authenticate, fetch and dedupe real posts.

    Credentials come from BSKY_HANDLE / BSKY_APP_PASSWORD. Returns [] on missing creds rather
    than raising, so the caller can degrade gracefully; per-query fetch errors are logged and
    skipped so one bad query doesn't sink the whole corpus. Deduped by post uri, capped at
    max_posts, keeping the order queries were derived in (triggers before context).
    """
    handle = os.environ.get("BSKY_HANDLE")
    password = os.environ.get("BSKY_APP_PASSWORD")
    if not handle or not password:
        logger.error("BSKY_HANDLE / BSKY_APP_PASSWORD not set; cannot fetch corpus")
        return []

    queries = derive_queries(spec)
    if not queries:
        logger.warning("Spec produced no searchable queries; corpus is empty")
        return []

    out: list[CorpusPost] = []
    seen_uris: set[str] = set()
    with httpx.Client(timeout=timeout) as client:
        session = authenticate(handle, password, client=client)
        access_jwt = session["accessJwt"]
        for query in queries:
            if len(out) >= max_posts:
                break
            try:
                data = search_posts(query["q"], access_jwt=access_jwt, client=client, limit=per_query)
            except Exception:
                logger.exception("searchPosts failed for query %r; skipping", query["q"])
                continue
            for post in data.get("posts") or []:
                uri = post.get("uri")
                if uri in seen_uris:
                    continue
                seen_uris.add(uri)
                out.append(_normalize(post, query["q"], query["bucket"]))
                if len(out) >= max_posts:
                    break

        # Fetch author metadata only when a rule needs it, so keyword/pattern labelers pay no extra
        # profile calls. Done inside the client block so the session/connection is still open.
        if out and spec_uses_account_signals(spec):
            enrich_accounts(out, access_jwt=access_jwt, client=client)

    logger.info("Fetched %d posts across %d queries for %s", len(out), len(queries), corpus_key(spec))
    return out[:max_posts]

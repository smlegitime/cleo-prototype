import { useState, type SubmitEvent } from "react";
import { getToken } from "../api";

const STORAGE_KEY_TOKEN = "stream_chat_token";
const STORAGE_KEY_USER_ID = "stream_chat_user_id";
const STORAGE_KEY_USER_NAME = "stream_chat_user_name";
const STORAGE_KEY_CHANNEL_ID = "stream_chat_channel_id";

export interface Auth {
  token: string;
  userId: string;
  userName: string;
  channelId: string;
}

/** The join code from the group's invite link (`?c=<code>`), or null if the link carried none. */
export function getJoinCodeFromUrl(): string | null {
  const code = new URLSearchParams(window.location.search).get("c");
  return code?.trim() || null;
}

export function getStoredAuth(): Auth | null {
  const token = sessionStorage.getItem(STORAGE_KEY_TOKEN);
  const userId = sessionStorage.getItem(STORAGE_KEY_USER_ID);
  const userName = sessionStorage.getItem(STORAGE_KEY_USER_NAME);
  const channelId = sessionStorage.getItem(STORAGE_KEY_CHANNEL_ID);
  if (token && userId && channelId) {
    return { token, userId, userName: userName ?? userId, channelId };
  }
  return null;
}

export function setStoredAuth(auth: Auth) {
  sessionStorage.setItem(STORAGE_KEY_TOKEN, auth.token);
  sessionStorage.setItem(STORAGE_KEY_USER_ID, auth.userId);
  sessionStorage.setItem(STORAGE_KEY_USER_NAME, auth.userName ?? auth.userId);
  sessionStorage.setItem(STORAGE_KEY_CHANNEL_ID, auth.channelId);
}

export function clearStoredAuth() {
  sessionStorage.removeItem(STORAGE_KEY_TOKEN);
  sessionStorage.removeItem(STORAGE_KEY_USER_ID);
  sessionStorage.removeItem(STORAGE_KEY_USER_NAME);
  sessionStorage.removeItem(STORAGE_KEY_CHANNEL_ID);
}

export function Onboarding({ onJoin }: { onJoin: (auth: Auth) => void }) {
  const [userName, setUserName] = useState("");
  // Only prompted for when the link didn't carry one — most people arrive via ?c=<code>.
  const linkCode = getJoinCodeFromUrl();
  const [joinCode, setJoinCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: SubmitEvent) => {
    e.preventDefault();
    const name = userName.trim();
    if (!name) {
      setError("Please enter a display name.");
      return;
    }
    setError(null);
    setLoading(true);
    try {
      const { token, user_id, user_name, channel_id } = await getToken(
        name,
        linkCode ?? joinCode
      );
      const auth: Auth = {
        token,
        userId: user_id,
        userName: user_name,
        channelId: channel_id,
      };
      setStoredAuth(auth);
      onJoin(auth);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to join. Try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        width: "100%",
        minHeight: "100vh",
        padding: "1.5rem",
        background: "var(--background-color, #242424)",
        color: "var(--color, rgba(255,255,255,0.87))",
        boxSizing: "border-box",
      }}
    >
      <h1 style={{ marginBottom: "0.5rem", fontSize: "1.75rem" }}>CLEO</h1>
      <p
        style={{
          marginBottom: "0.75rem",
          opacity: 0.9,
          fontSize: "1.05rem",
          textAlign: "center",
          maxWidth: 460,
        }}
      >
        A group chat assistant for designing your community's Bluesky labeler.
      </p>
      <p
        style={{
          marginBottom: "1.25rem",
          opacity: 0.7,
          fontSize: "0.9rem",
          lineHeight: 1.5,
          textAlign: "center",
          maxWidth: 460,
        }}
      >
        Work with your group to decide what your labeler flags, preview how it behaves on real
        posts, and test it privately in a sandbox. No Bluesky account needed to start.
      </p>
      <p
        style={{
          marginBottom: "1.5rem",
          opacity: 0.5,
          fontSize: "0.72rem",
          textTransform: "uppercase",
          letterSpacing: "0.08em",
        }}
      >
        Research prototype
      </p>
      <form onSubmit={handleSubmit} style={{ width: "100%", maxWidth: 320 }}>
        <input
          type="text"
          placeholder="Display name"
          value={userName}
          onChange={(e) => setUserName(e.target.value)}
          disabled={loading}
          autoFocus
          style={{
            width: "100%",
            padding: "0.75rem 1rem",
            marginBottom: "0.75rem",
            fontSize: "1rem",
            borderRadius: 8,
            border: "1px solid rgba(255,255,255,0.2)",
            background: "rgba(255,255,255,0.05)",
            color: "inherit",
            boxSizing: "border-box",
          }}
        />
        {!linkCode && (
          <input
            type="text"
            placeholder="Channel ID"
            value={joinCode}
            onChange={(e) => setJoinCode(e.target.value)}
            disabled={loading}
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            style={{
              width: "100%",
              padding: "0.75rem 1rem",
              marginBottom: "0.75rem",
              fontSize: "1rem",
              borderRadius: 8,
              border: "1px solid rgba(255,255,255,0.2)",
              background: "rgba(255,255,255,0.05)",
              color: "inherit",
              boxSizing: "border-box",
            }}
          />
        )}
        {linkCode && (
          <p
            style={{
              opacity: 0.6,
              fontSize: "0.8rem",
              marginBottom: "0.75rem",
              textAlign: "center",
            }}
          >
            Joining <strong>{linkCode}</strong>
          </p>
        )}
        {error && (
          <p
            style={{
              color: "#f88",
              fontSize: "0.875rem",
              marginBottom: "0.75rem",
            }}
          >
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={loading}
          style={{
            width: "100%",
            padding: "0.75rem 1rem",
            fontSize: "1rem",
            fontWeight: 600,
            borderRadius: 8,
            border: "none",
            background: "#0a7ea4",
            color: "#fff",
            cursor: loading ? "not-allowed" : "pointer",
            opacity: loading ? 0.7 : 1,
          }}
        >
          {loading ? "Joining…" : "Join"}
        </button>
      </form>
    </div>
  );
}

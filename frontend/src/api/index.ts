const baseApiUrl = import.meta.env.VITE_AI_ASSISTANT_URL ?? "";

function getBaseUrl(): string {
  const url = typeof baseApiUrl === "string" ? baseApiUrl : "";
  if (!url.length) throw new Error("Missing VITE_AI_ASSISTANT_URL");
  return url;
}

export interface TokenResponse {
  token: string;
  user_id: string;
  user_name: string;
  channel_id: string;
}

/**
 * Get a Stream token for the given display name, joining the channel named by `joinCode` (the
 * `?c=` value from the group's invite link). Unknown codes are rejected by the backend; omitting
 * the code lands the user in the deployment's default channel.
 */
export const getToken = async (
  userName: string,
  joinCode?: string | null
): Promise<TokenResponse> => {
  const res = await fetch(`${getBaseUrl()}/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_name: userName.trim(),
      channel_id: joinCode?.trim() || null,
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(typeof err.detail === "string" ? err.detail : "Failed to get token");
  }
  return res.json();
};


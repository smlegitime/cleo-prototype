import { useState, useCallback } from "react";
import type { ChannelFilters, ChannelOptions, ChannelSort } from "stream-chat";
import { Chat, useCreateChatClient } from "stream-chat-react";

import { ChatContent } from "./components/ChatContent";
import {
  Onboarding,
  clearStoredAuth,
  getJoinCodeFromUrl,
  getStoredAuth,
  type Auth,
} from "./components/Onboarding";

const rawApiKey = import.meta.env.VITE_STREAM_API_KEY;
if (typeof rawApiKey !== "string" || !rawApiKey.length) {
  throw new Error("Missing VITE_STREAM_API_KEY");
}
const apiKey: string = rawApiKey;

const options: ChannelOptions = { limit: 5 };
const sort: ChannelSort = { pinned_at: 1, last_message_at: -1, updated_at: -1 };

function ChatApp({ token, userId, userName, channelId }: Auth) {
  const chatClient = useCreateChatClient({
    apiKey,
    tokenOrProvider: token,
    userData: { id: userId, name: userName },
  });

  if (!chatClient) {
    return <div className="app-loading">Loading chat…</div>;
  }

  const filters: ChannelFilters = {
    type: "messaging",
    members: { $in: [userId] },
    archived: false,
  };

  return (
    <Chat client={chatClient} theme="str-chat__theme-dark">
      <ChatContent
        channelId={channelId}
        filters={filters}
        sort={sort}
        options={options}
      />
    </Chat>
  );
}

/**
 * Session auth, unless the link points at a different group than the stored session — following a
 * second group's invite in the same tab should re-onboard, not silently reuse the first identity.
 */
function initialAuth(): Auth | null {
  const stored = getStoredAuth();
  const linkCode = getJoinCodeFromUrl();
  if (
    stored &&
    linkCode &&
    stored.channelId.toLowerCase() !== linkCode.toLowerCase()
  ) {
    clearStoredAuth();
    return null;
  }
  return stored;
}

export default function App() {
  const [auth, setAuth] = useState<Auth | null>(initialAuth);

  const handleJoin = useCallback((joined: Auth) => {
    setAuth(joined);
  }, []);

  if (!auth) {
    return <Onboarding onJoin={handleJoin} />;
  }

  return (
    <div className="app">
      <ChatApp {...auth} />
    </div>
  );
}

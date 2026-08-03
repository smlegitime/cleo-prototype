import { useChannelStateContext } from "stream-chat-react";

/**
 * "Clear channel" control — resets the active channel for a fresh demo: truncates its messages and
 * deletes its checkpoint thread via POST /clear-channel/<id> (chatbot.py). Hidden for protected
 * channels (VITE_PROTECTED_CHANNELS, default "dev2") so the preserved demo can't be wiped; the
 * backend enforces the same list independently. Stream emits a channel.truncated event, so the
 * message list clears on its own.
 */

const API_BASE = (import.meta.env.VITE_AI_ASSISTANT_URL as string | undefined) ?? "";
const PROTECTED = ((import.meta.env.VITE_PROTECTED_CHANNELS as string | undefined) ?? "dev2")
	.split(",")
	.map((s) => s.trim())
	.filter(Boolean);

export function ClearChannelButton() {
	const { channel } = useChannelStateContext();
	const id = channel?.id ?? "";
	if (!id || PROTECTED.includes(id)) return null;

	const onClear = async () => {
		if (!window.confirm(`Clear all messages and reset "${id}"? This can't be undone.`)) return;
		try {
			const res = await fetch(`${API_BASE}/clear-channel/${encodeURIComponent(id)}`, {
				method: "POST",
			});
			if (!res.ok) throw new Error(String(res.status));
		} catch {
			window.alert("Couldn't clear the channel. Please try again.");
		}
	};

	return (
		<div style={{ display: "flex", justifyContent: "flex-end", padding: "0.25rem 1rem" }}>
			<button
				onClick={onClear}
				title="Clear all messages and reset this channel"
				style={{
					fontSize: "0.75rem",
					color: "#f88",
					background: "transparent",
					border: "1px solid rgba(255,136,136,0.4)",
					borderRadius: 6,
					padding: "0.2rem 0.6rem",
					cursor: "pointer",
				}}
			>
				Clear channel
			</button>
		</div>
	);
}

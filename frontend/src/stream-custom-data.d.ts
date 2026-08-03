import { DefaultMessageData, type DefaultChannelData } from "stream-chat-react";

declare module "stream-chat" {
	interface CustomMessageData extends DefaultMessageData {
		ai_generated?: boolean;
		/**
		 * Vote state of a card the group has to approve, set by the backend (src/api/stream.py).
		 * Absent on ordinary messages — only anchors carry it. "superseded" means a newer proposal
		 * replaced this one and reacting to it does nothing.
		 */
		approval_state?: "pending" | "approved" | "superseded";
	}
	interface CustomChannelData extends DefaultChannelData {}
}
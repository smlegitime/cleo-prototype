import { StreamingMessage } from "@stream-io/chat-react-ai";
import {
	Attachment,
	Avatar,
	MessageErrorIcon,
	MessageOptions,
	MessageTimestamp,
	ReactionsList,
	useMessageContext,
} from "stream-chat-react";
import type { LocalMessage } from "stream-chat";

function messageHasReactions(message?: LocalMessage): boolean {
	return Object.values(message?.reaction_groups ?? {}).some(
		(g: { count?: number }) => (g?.count ?? 0) > 0
	);
}
import clsx from "clsx";

export const MessageBubble = () => {
	const { message, isMyMessage, highlighted, handleAction, endOfGroup, groupedByUser, groupStyles } =
		useMessageContext();

	const attachments = message?.attachments || [];
	const hasAttachments = attachments.length > 0;
	const hasReactions = messageHasReactions(message);
	const user = message?.user;
	const senderName = isMyMessage()
		? "You"
		: (user?.name ?? user?.id ?? "Unknown");

	// Show sender name and timestamp only on the last message in a group (use groupStyles from list)
	const isLastInGroup =
		endOfGroup === true ||
		(groupStyles?.length
			? groupStyles.includes("bottom") || groupStyles.includes("single")
			: true);
	const showMeta = groupedByUser === false ? true : isLastInGroup;

	// Cards the group votes on get an accent so they're findable when scrolling back. Only the
	// backend's anchors carry approval_state, and it's retagged when the vote resolves, so an
	// approved card stops advertising itself as needing attention.
	const approvalState = message?.approval_state;

	const rootClassName = clsx(
		"str-chat__message str-chat__message-simple",
		`str-chat__message--${message.type}`,
		`str-chat__message--${message.status}`,
		{
			"str-chat__message--me": isMyMessage(),
			"str-chat__message--other": !isMyMessage(),
			"str-chat__message--has-attachment": hasAttachments,
			"str-chat__message--with-reactions": hasReactions,
			"str-chat__message--highlighted": highlighted,
			"str-chat__message-send-can-be-retried":
				message?.status === "failed" && message?.error?.status !== 403,
		},
		approvalState && `cleo-message--approval-${approvalState}`
	);

	return (
		<div className={rootClassName}>
			{user && (
				user.id === "ai-assistant" || user.id?.startsWith("ai-") ? (
					<div className="str-chat__message-avatar str-chat__message-avatar--ai" aria-label="AI Assistant">
						🤖
					</div>
				) : (
					<Avatar
						image={user.image}
						name={user.name ?? user.id}
						user={user}
						className="str-chat__message-avatar"
					/>
				)
			)}
			<div className="str-chat__message-inner" data-testid="message-inner">
				<div className="str-chat__message-bubble-row">
					<div className="str-chat__message-bubble">
						{showMeta && !isMyMessage() && (
							<span className="str-chat__message-sender-name">
								{senderName}
							</span>
						)}
						{hasAttachments && (
							<Attachment
								actionHandler={handleAction}
								attachments={attachments}
							/>
						)}
						{message?.text && <StreamingMessage text={message.text} />}
						<MessageErrorIcon />
						{showMeta && (
							<MessageTimestamp customClass="str-chat__message-timestamp" format="h:mm A" calendar={false} />
						)}
					</div>
					<div className="str-chat__message-actions-wrapper">
						<MessageOptions />
					</div>
				</div>
				{hasReactions && (
					<div className="str-chat__message-reactions-host">
						<ReactionsList />
					</div>
				)}
				</div>
		</div>
	);
};
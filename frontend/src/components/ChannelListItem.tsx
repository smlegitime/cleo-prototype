import type { ChannelPreviewUIComponentProps } from "stream-chat-react";
import { useChatContext } from "stream-chat-react";
import clsx from "clsx";

export const ChannelListItem = (props: ChannelPreviewUIComponentProps) => {
	const { id, data } = props.channel;
	const { setActiveChannel, channel: activeChannel } = useChatContext();
	const isActive = activeChannel?.id === id;

	const lastMessage = props.lastMessage;
	const lastMessageText = lastMessage?.text?.trim();
	const lastMessageSender = lastMessage?.user?.name ?? lastMessage?.user?.id;

	return (
		<div
			className={clsx("tut__channel-preview", {
				"tut__channel-preview--active": isActive,
			})}
			onClick={() => setActiveChannel(props.channel)}
		>
			<div className="tut__channel-preview__name">
				{data?.name ?? id ?? "General"}
			</div>
			{lastMessageText && (
				<div className="tut__channel-preview__last-message">
					{lastMessageSender && (
						<span className="tut__channel-preview__last-message-sender">
							{lastMessageSender}:&nbsp;
						</span>
					)}
					{lastMessageText}
				</div>
			)}
		</div>
	);
};
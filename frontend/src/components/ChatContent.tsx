import {
	Channel,
	ChannelHeader,
	MessageList,
	ChannelList,
	Window,
	MessageInput,
	useChatContext,
	type ChannelListProps,
} from "stream-chat-react";
import { useEffect } from "react";
import { Composer } from "./Composer";
import { MessageBubble } from "./MessageBubble";
import { AIStateIndicator } from "./AIStateIndicator";
import { ChannelMembers } from "./ChannelMembers";
import { ClearChannelButton } from "./ClearChannelButton";
import { ChannelListItem } from "./ChannelListItem";

// Only reactions the backend acts on. ❤️ used to sit next to 👍🏾 here and did nothing: the webhook
// drops every type but "like", silently, so someone who hearted a proposal believed they had
// approved it while the tally recorded an abstention. A picker with no inert options can't
// misreport consent that way.
const reactionOptions = [
	// { type: "upvote", Component: () => <>⬆️</> },
	// { type: "question", Component: () => <>❓</> },
	{ type: "like", Component: () => <>👍🏾</> },
	{ type: "summon", Component: () => <>🤖</> },
];

export const ChatContent = ({
	channelId,
	filters,
	options,
	sort,
}: Pick<ChannelListProps, "options" | "sort" | "filters"> & {
	channelId: string;
}) => {
	const { setActiveChannel, client, channel } = useChatContext();

	useEffect(() => {
		if (!channel) {
			setActiveChannel(client.channel("messaging", channelId));
		}
	}, [channel, channelId, client, setActiveChannel]);

	return (
		<>
			<ChannelList
				Preview={ChannelListItem}
				setActiveChannelOnMount={false}
				filters={filters}
				sort={sort}
				options={options}
			/>
			<Channel initializeOnMount={false} Message={MessageBubble} reactionOptions={reactionOptions}>
				<Window>
					<ChannelHeader />
					<ChannelMembers />
					<ClearChannelButton />
					<MessageList />
					<AIStateIndicator />
					<MessageInput Input={Composer} />
				</Window>
			</Channel>
		</>
	);
};

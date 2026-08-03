import { useEffect, useState } from "react";
import { useChannelStateContext } from "stream-chat-react";
import type { ChannelMemberResponse } from "stream-chat";

/**
 * Displays the list of users who have joined the channel.
 */
export function ChannelMembers() {
	const { channel, members: contextMembers } = useChannelStateContext("ChannelMembers");
	const [queriedMembers, setQueriedMembers] = useState<ChannelMemberResponse[] | null>(null);

	const fromState = contextMembers ?? channel?.state?.members ?? {};
	const stateList = Object.values(fromState) as ChannelMemberResponse[];

	// If state has no members yet, query once so we show who's in the channel
	useEffect(() => {
		if (!channel?.initialized || stateList.length > 0) {
			if (stateList.length > 0) setQueriedMembers(null);
			return;
		}
		let cancelled = false;
		channel
			.queryMembers({})
			.then((res) => {
				if (!cancelled && res.members) setQueriedMembers(res.members);
			})
			.catch(() => {});
		return () => {
			cancelled = true;
		};
	}, [channel?.cid, channel?.initialized, stateList.length]);

	const list =
		stateList.length > 0 ? stateList : queriedMembers ?? stateList;

	if (list.length === 0) {
		return null;
	}

	return (
		<div className="str-chat__channel-members">
			<p className="str-chat__channel-members-title">
				In this channel ({list.length})
			</p>
			<ul className="str-chat__channel-members-list" role="list">
				{list.map((m) => {
					const userId = m.user_id ?? m.user?.id ?? "";
					const name = m.user?.name ?? userId;
					const isAI = userId === "ai-assistant" || userId.startsWith("ai-");
					return (
						<li key={userId} className="str-chat__channel-members-item">
							{name || userId || "-"}
							{isAI && (
								<span className="str-chat__channel-members-ai-badge">AI</span>
							)}
						</li>
					);
				})}
			</ul>
		</div>
	);
}

import { useEffect } from 'react';
import {
  AIStates,
  useAIState,
  useChannelStateContext,
} from 'stream-chat-react';
import { AIStateIndicator as StateIndicator } from '@stream-io/chat-react-ai';

const scrollListToBottom = () => {
  const list = document.querySelector('.str-chat__list');
  if (list) list.scrollTop = list.scrollHeight;
};

export const AIStateIndicator = () => {
  const { channel } = useChannelStateContext();
  const { aiState } = useAIState(channel);

  const isActive = [AIStates.Generating, AIStates.Thinking].includes(aiState);

  useEffect(() => {
    if (isActive) scrollListToBottom();
  }, [isActive]);

  useEffect(() => {
    if (!isActive) return;
    channel.on('message.updated', scrollListToBottom);
    return () => channel.off('message.updated', scrollListToBottom);
  }, [isActive, channel]);

  // Scroll to bottom as the typewriter animation grows the message bubble between server updates
  useEffect(() => {
    if (!isActive) return;
    const list = document.querySelector('.str-chat__list');
    const content = list?.querySelector('.str-chat__ul');
    if (!list || !content) return;
    const observer = new ResizeObserver(() => {
      list.scrollTop = list.scrollHeight;
    });
    observer.observe(content);
    return () => observer.disconnect();
  }, [isActive]);

  if (!isActive) return null;

  return <StateIndicator key={channel.state.last_message_at?.toString()} />;
};

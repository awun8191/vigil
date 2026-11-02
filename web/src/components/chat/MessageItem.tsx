import { motion } from 'framer-motion';
import type { ChatMessage } from '@/types';
import { Message } from '@/components/ui/c-message-3-utils/message';
import { Bubble, BubbleContent } from '@/components/ui/c-message-3-utils/bubble';

const enter = {
  initial: { opacity: 0, y: 12 },
  animate: { opacity: 1, y: 0 },
  transition: { type: 'spring' as const, stiffness: 400, damping: 30 },
};

interface Props {
  message: ChatMessage;
  /** Set when this message opens a new day, rendered as a labelled divider. */
  divider?: string;
}

export function MessageItem({ message, divider }: Props) {
  const mine = message.role === 'user';

  return (
    <motion.div {...enter} className="space-y-1.5">
      {divider ? <Divider label={divider} /> : null}
      <Message align={mine ? 'end' : 'start'}>
        <Bubble variant={mine ? 'default' : 'muted'}>
          <BubbleContent>{message.content}</BubbleContent>
        </Bubble>
      </Message>
    </motion.div>
  );
}

function Divider({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-4 text-[12px] text-[var(--text-muted)]">
      <span className="h-px flex-1 bg-[color-mix(in_srgb,var(--text)_10%,transparent)]" />
      {label}
      <span className="h-px flex-1 bg-[color-mix(in_srgb,var(--text)_10%,transparent)]" />
    </div>
  );
}

export default MessageItem;

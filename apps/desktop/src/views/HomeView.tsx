/**
 * HomeView — the default landing route inside the workspace.
 * Renders the natural-language chat flow (messages + input).
 */
import { CenterChatFlow } from '@/layouts/CenterChatFlow';

export function HomeView(): JSX.Element {
  return <CenterChatFlow />;
}
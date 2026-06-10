import { useState } from 'react';
import { Chat } from './components/Chat';
import { SuspenseDemo } from './demos/SuspenseDemo';
import { TransitionDemo } from './demos/TransitionDemo';
import { OptimisticDemo } from './demos/OptimisticDemo';
import { MemoDemo } from './demos/MemoDemo';
import { assertNever } from './types/agentEvents';

const TABS = ['chat', 'suspense', 'transition', 'optimistic', 'memo'] as const;
type Tab = (typeof TABS)[number];

function TabContent({ tab }: { tab: Tab }) {
  switch (tab) {
    case 'chat':
      return <Chat />;
    case 'suspense':
      return <SuspenseDemo />;
    case 'transition':
      return <TransitionDemo />;
    case 'optimistic':
      return <OptimisticDemo />;
    case 'memo':
      return <MemoDemo />;
    default:
      return assertNever(tab);
  }
}

export default function App() {
  const [tab, setTab] = useState<Tab>('chat');
  return (
    <main className="app">
      <h1>Agentic UI — Interview Prep</h1>
      <nav className="tabs" aria-label="Demos">
        {TABS.map((t) => (
          <button
            key={t}
            type="button"
            className={`tab ${t === tab ? 'tab--active' : ''}`}
            aria-pressed={t === tab}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </nav>
      <TabContent tab={tab} />
    </main>
  );
}

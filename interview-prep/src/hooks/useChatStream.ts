/**
 * INTERVIEW TOPIC: stream handling + state management for an agent chat.
 *
 * Design decisions to narrate on the whiteboard:
 *  - useReducer, not scattered useState: the stream is a sequence of events,
 *    so state transitions belong in one pure, testable function. (Same
 *    reasoning as the feedbackMachine in the bunq assignment.)
 *  - The reducer "upserts" the assistant message on each text_delta, so the
 *    UI doesn't care whether deltas arrive before or after run_started.
 *  - Status is a finite set ('idle' | 'streaming' | 'awaiting_approval' |
 *    'error'), which makes illegal UI states (e.g. input enabled while a tool
 *    awaits approval) unrepresentable.
 *  - Cancellation: keep a handle to the active generator and call .return()
 *    — the moral equivalent of AbortController on a fetch stream.
 */
import { useCallback, useReducer, useRef } from 'react';
import type { AgentStreamEvent, ChatMessage, ToolCall } from '../types/agentEvents';
import { assertNever } from '../types/agentEvents';
import { runAgent, resumeAgent } from '../server/mockAgent';

export type ChatStatus = 'idle' | 'streaming' | 'awaiting_approval' | 'error';

export interface ChatState {
  messages: ChatMessage[];
  status: ChatStatus;
  error: string | null;
}

type ChatAction =
  | { type: 'user_message'; id: string; content: string }
  | { type: 'agent_event'; event: AgentStreamEvent }
  | { type: 'tool_decision'; toolCallId: string; decision: 'approved' | 'rejected' }
  | { type: 'stream_aborted' };

const initialState: ChatState = { messages: [], status: 'idle', error: null };

function applyAgentEvent(state: ChatState, event: AgentStreamEvent): ChatState {
  switch (event.type) {
    case 'run_started':
      return { ...state, status: 'streaming', error: null };

    case 'text_delta': {
      const exists = state.messages.some(
        (m) => m.role === 'assistant' && m.id === event.messageId,
      );
      const messages = exists
        ? state.messages.map((m) =>
            m.role === 'assistant' && m.id === event.messageId
              ? { ...m, content: m.content + event.delta }
              : m,
          )
        : [
            ...state.messages,
            { id: event.messageId, role: 'assistant' as const, content: event.delta, streaming: true },
          ];
      return { ...state, messages };
    }

    case 'tool_call':
      return {
        ...state,
        status: 'awaiting_approval',
        messages: [
          ...state.messages.map((m) =>
            m.role === 'assistant' ? { ...m, streaming: false } : m,
          ),
          { id: event.toolCall.id, role: 'tool', toolCall: event.toolCall, status: 'pending' },
        ],
      };

    case 'tool_result':
      return {
        ...state,
        messages: state.messages.map((m) =>
          m.role === 'tool' && m.toolCall.id === event.toolCallId
            ? { ...m, result: event.result }
            : m,
        ),
      };

    case 'run_finished':
      return {
        ...state,
        status: 'idle',
        messages: state.messages.map((m) =>
          m.role === 'assistant' ? { ...m, streaming: false } : m,
        ),
      };

    case 'run_error':
      return { ...state, status: 'error', error: event.message };

    default:
      // Compile error here if a new event variant is ever left unhandled.
      return assertNever(event);
  }
}

function reducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case 'user_message':
      return {
        ...state,
        status: 'streaming',
        error: null,
        messages: [...state.messages, { id: action.id, role: 'user', content: action.content }],
      };
    case 'agent_event':
      return applyAgentEvent(state, action.event);
    case 'tool_decision':
      return {
        ...state,
        status: 'streaming',
        messages: state.messages.map((m) =>
          m.role === 'tool' && m.toolCall.id === action.toolCallId
            ? { ...m, status: action.decision }
            : m,
        ),
      };
    case 'stream_aborted':
      return {
        ...state,
        status: 'idle',
        messages: state.messages.map((m) =>
          m.role === 'assistant' ? { ...m, streaming: false } : m,
        ),
      };
    default:
      return assertNever(action);
  }
}

export function useChatStream(options?: { failureRate?: number }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const activeStream = useRef<AsyncGenerator<AgentStreamEvent> | null>(null);

  const pump = useCallback(async (gen: AsyncGenerator<AgentStreamEvent>) => {
    activeStream.current = gen;
    try {
      for await (const event of gen) {
        dispatch({ type: 'agent_event', event });
      }
    } catch (err) {
      // Network/transport failure — distinct from a run_error the agent reports.
      dispatch({
        type: 'agent_event',
        event: {
          type: 'run_error',
          message: err instanceof Error ? err.message : 'Stream failed',
          retryable: true,
        },
      });
    } finally {
      if (activeStream.current === gen) activeStream.current = null;
    }
  }, []);

  const send = useCallback(
    (text: string) => {
      dispatch({ type: 'user_message', id: `user_${Date.now()}`, content: text });
      void pump(runAgent(text, options));
    },
    [pump, options],
  );

  const decideTool = useCallback(
    (toolCall: ToolCall, decision: 'approved' | 'rejected') => {
      dispatch({ type: 'tool_decision', toolCallId: toolCall.id, decision });
      void pump(resumeAgent(toolCall, decision));
    },
    [pump],
  );

  const stop = useCallback(() => {
    void activeStream.current?.return(undefined);
    activeStream.current = null;
    dispatch({ type: 'stream_aborted' });
  }, []);

  return { ...state, send, decideTool, stop };
}

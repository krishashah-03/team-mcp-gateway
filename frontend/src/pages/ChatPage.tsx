import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { api, ApiError, type ChatMessage, type ToolCallTrace } from "../lib/api";
import { formatDuration } from "../lib/format";
import { messageOf, useLoad } from "../lib/useLoad";
import { Badge, Button, ConfirmButton, ErrorNote, Loading } from "../components/ui";

const EXAMPLES = [
  "What orders came in today?",
  "Create a product called Blue Mug priced at 349",
  "What is our return policy?",
];

export function ToolTrace({ calls }: { calls: ToolCallTrace[] }) {
  if (calls.length === 0) return null;
  return (
    <div className="trace" aria-label="Tools used">
      <p className="trace-title">Used {calls.length === 1 ? "1 tool" : `${calls.length} tools`}</p>
      {calls.map((call, index) => (
        <details key={index} className={`trace-item ${call.is_error ? "trace-error" : ""}`}>
          <summary>
            <code>{call.name}</code>
            <span className="muted"> {formatDuration(call.duration_ms)}</span>
            {call.is_error ? <Badge tone="red">failed</Badge> : null}
          </summary>
          <p className="trace-label">Arguments</p>
          <pre>{JSON.stringify(call.arguments, null, 2)}</pre>
          <p className="trace-label">Result</p>
          <pre>{call.result_preview || "(empty)"}</pre>
        </details>
      ))}
    </div>
  );
}

export default function ChatPage() {
  const conversations = useLoad(() => api.conversations());
  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadingThread, setLoadingThread] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  // Ignore a thread that finishes loading after the user already moved on.
  const threadRequest = useRef(0);

  useEffect(() => {
    bottom.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [messages, sending]);

  async function open(id: number | null) {
    setError(null);
    setActiveId(id);
    const request = ++threadRequest.current;
    if (id === null) {
      setMessages([]);
      return;
    }
    setLoadingThread(true);
    try {
      const thread = await api.conversation(id);
      if (request === threadRequest.current) setMessages(thread);
    } catch (caught) {
      if (request === threadRequest.current) setError(messageOf(caught));
    } finally {
      if (request === threadRequest.current) setLoadingThread(false);
    }
  }

  async function send(text: string) {
    const message = text.trim();
    if (!message || sending) return;
    setError(null);
    setDraft("");
    setSending(true);
    const pending: ChatMessage = { id: -Date.now(), role: "user", content: message, tool_calls: null, created_at: null };
    setMessages((current) => [...current, pending]);
    try {
      const result = await api.chat(message, activeId ?? undefined);
      threadRequest.current++;
      setActiveId(result.conversation_id);
      setMessages((current) => [
        ...current,
        { id: -Date.now() - 1, role: "assistant", content: result.reply, tool_calls: result.tool_calls, created_at: null },
      ]);
      void conversations.reload();
    } catch (caught) {
      // Take the unsent message back out and give the text back so nothing is lost.
      setMessages((current) => current.filter((entry) => entry.id !== pending.id));
      setDraft(message);
      setError(
        caught instanceof ApiError && caught.status === 503
          ? `${caught.message} Ask an administrator to configure it.`
          : messageOf(caught),
      );
    } finally {
      setSending(false);
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void send(draft);
    }
  }

  async function remove(id: number) {
    try {
      await api.deleteConversation(id);
      if (id === activeId) await open(null);
      await conversations.reload();
    } catch (caught) {
      setError(messageOf(caught));
    }
  }

  return (
    <div className="chat">
      <aside className="chat-list" aria-label="Conversations">
        <Button variant="primary" onClick={() => void open(null)}>
          New conversation
        </Button>
        {conversations.loading && !conversations.data ? <Loading /> : null}
        <ul>
          {conversations.data?.map((conversation) => (
            <li key={conversation.id} className={conversation.id === activeId ? "active" : ""}>
              <button className="chat-item" onClick={() => void open(conversation.id)}>
                {conversation.title}
              </button>
              {conversation.id === activeId ? (
                <ConfirmButton confirmLabel="Delete" onConfirm={() => remove(conversation.id)}>
                  Delete
                </ConfirmButton>
              ) : null}
            </li>
          ))}
        </ul>
      </aside>

      <section className="chat-main">
        <div className="chat-scroll" aria-live="polite">
          {loadingThread ? <Loading label="Opening conversation" /> : null}
          {!loadingThread && messages.length === 0 ? (
            <div className="chat-welcome">
              <h1>Ask about your store</h1>
              <p className="muted">
                The assistant answers using the same tools you can use from an MCP client, and shows which ones it
                called.
              </p>
              <div className="examples">
                {EXAMPLES.map((example) => (
                  <button key={example} className="chip" onClick={() => void send(example)} disabled={sending}>
                    {example}
                  </button>
                ))}
              </div>
            </div>
          ) : null}

          {messages.map((message) => (
            <div key={message.id} className={`bubble bubble-${message.role}`}>
              <p className="bubble-role">{message.role === "user" ? "You" : "Assistant"}</p>
              <p className="bubble-text">{message.content}</p>
              {message.role === "assistant" && message.tool_calls ? <ToolTrace calls={message.tool_calls} /> : null}
            </div>
          ))}
          {sending ? (
            <div className="bubble bubble-assistant">
              <Loading label="Thinking" />
            </div>
          ) : null}
          <div ref={bottom} />
        </div>

        <ErrorNote message={error} />
        <form
          className="composer"
          onSubmit={(event: FormEvent) => {
            event.preventDefault();
            void send(draft);
          }}
        >
          <textarea
            aria-label="Message"
            placeholder="Ask a question…  (Enter to send, Shift+Enter for a new line)"
            rows={2}
            maxLength={4000}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={onKeyDown}
          />
          <Button type="submit" variant="primary" busy={sending} disabled={!draft.trim()}>
            Send
          </Button>
        </form>
      </section>
    </div>
  );
}

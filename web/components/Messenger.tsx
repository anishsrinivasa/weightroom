"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { useAuth } from "@clerk/nextjs";

import { keystoneRequest } from "@/lib/api";
import {
  messageSchema,
  threadDeletedSchema,
  threadDetailSchema,
  threadListSchema,
  threadSummarySchema,
  type Message,
  type ThreadDetail,
  type ThreadSummary,
} from "@/lib/contracts";

/**
 * Messaging lives in one place at the bottom right, and the per-model chat
 * button opens the same panel rather than a second one somewhere else.
 *
 * A context rather than props: the launcher is mounted once in the layout and
 * the buttons that open a conversation are scattered across pages that know
 * nothing about it.
 */
type MessengerApi = { openListing: (listingId: string) => void };

const MessengerContext = createContext<MessengerApi | null>(null);

/** Opens a conversation about a listing, if the messenger is mounted. */
export function useMessenger(): MessengerApi {
  return useContext(MessengerContext) ?? { openListing: () => undefined };
}

const POLL_MS = 8_000;

export function MessengerProvider({ children }: { children: React.ReactNode }) {
  const { isSignedIn } = useAuth();
  const [open, setOpen] = useState(false);
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [active, setActive] = useState<ThreadDetail | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const foot = useRef<HTMLDivElement>(null);

  const loadThreads = useCallback(async () => {
    if (!isSignedIn) return;
    try {
      setThreads((await keystoneRequest("/v1/threads", threadListSchema)).threads);
    } catch {
      // A failed refresh keeps whatever is on screen: an empty list would
      // read as "your conversations are gone".
    }
  }, [isSignedIn]);

  const openThread = useCallback(async (threadId: string) => {
    setError(null);
    try {
      setActive(await keystoneRequest(
        `/v1/threads/${encodeURIComponent(threadId)}`, threadDetailSchema,
      ));
      void loadThreads();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not open that conversation");
    }
  }, [loadThreads]);

  const openListing = useCallback(async (listingId: string) => {
    setOpen(true);
    setError(null);
    try {
      const summary = await keystoneRequest(
        `/v1/listings/${encodeURIComponent(listingId)}/threads`,
        threadSummarySchema,
        { method: "POST" },
      );
      await openThread(summary.thread_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not start that conversation");
    }
  }, [openThread]);

  // Deferred rather than called in the effect body: setting state synchronously
  // there cascades renders, and the rest of the app defers the same way.
  useEffect(() => { queueMicrotask(() => void loadThreads()); }, [loadThreads]);

  useEffect(() => {
    // Polled, not pushed. A socket for a marketplace this size is a second
    // deployment concern for a message that can wait eight seconds.
    if (!isSignedIn) return;
    const timer = window.setInterval(() => {
      void loadThreads();
      if (active) void openThread(active.thread_id);
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [active, isSignedIn, loadThreads, openThread]);

  useEffect(() => {
    foot.current?.scrollIntoView({ block: "end" });
  }, [active?.messages.length]);

  async function send() {
    const body = draft.trim();
    if (!body || !active || busy) return;
    setBusy(true);
    // Shown immediately with a provisional id, so typing does not feel like it
    // went nowhere while the round trip completes.
    const pending: Message = {
      message_id: `pending-${Date.now()}`,
      sender_id: "", mine: true, body, created_at: new Date().toISOString(),
    };
    setActive({ ...active, messages: [...active.messages, pending] });
    setDraft("");
    try {
      const saved = await keystoneRequest(
        `/v1/threads/${encodeURIComponent(active.thread_id)}/messages`,
        messageSchema,
        { method: "POST", body: JSON.stringify({ body }) },
      );
      setActive((current) => current && {
        ...current,
        messages: current.messages.map((m) => m.message_id === pending.message_id ? saved : m),
      });
      void loadThreads();
    } catch (caught) {
      // Put the text back in the box rather than losing it to a failed send.
      setActive((current) => current && {
        ...current,
        messages: current.messages.filter((m) => m.message_id !== pending.message_id),
      });
      setDraft(body);
      setError(caught instanceof Error ? caught.message : "Message not sent");
    } finally {
      setBusy(false);
    }
  }

  async function remove(threadId: string) {
    setBusy(true);
    try {
      await keystoneRequest(
        `/v1/threads/${encodeURIComponent(threadId)}`, threadDeletedSchema,
        { method: "DELETE" },
      );
      setActive(null);
      await loadThreads();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not delete that conversation");
    } finally {
      setBusy(false);
    }
  }

  const unread = threads.reduce((total, thread) => total + thread.unread, 0);

  return (
    <MessengerContext.Provider value={{ openListing: (id) => void openListing(id) }}>
      {children}
      {isSignedIn ? (
        <div className="messenger">
          {open ? (
            <section className="messenger-panel" aria-label="Messages">
              <header className="messenger-head">
                {active ? (
                  <button className="text-button" type="button" onClick={() => setActive(null)}>
                    ← All conversations
                  </button>
                ) : <strong>Messages</strong>}
                <button
                  className="messenger-close"
                  type="button"
                  aria-label="Close messages"
                  onClick={() => setOpen(false)}
                >
                  ×
                </button>
              </header>

              {error ? <p className="messenger-error">{error}</p> : null}

              {active ? (
                <>
                  <div className="messenger-thread-title">
                    <strong>{active.listing_title || "Untitled model"}</strong>
                    <button
                      className="text-button"
                      type="button"
                      disabled={busy}
                      onClick={() => void remove(active.thread_id)}
                    >
                      Delete
                    </button>
                  </div>
                  <div className="messenger-messages">
                    {active.messages.length === 0 ? (
                      <p className="messenger-empty">No messages yet. Say hello.</p>
                    ) : active.messages.map((message) => (
                      <p
                        key={message.message_id}
                        className={`messenger-bubble ${message.mine ? "mine" : ""}`}
                      >
                        {message.body}
                      </p>
                    ))}
                    <div ref={foot} />
                  </div>
                  <form
                    className="messenger-compose"
                    onSubmit={(event) => { event.preventDefault(); void send(); }}
                  >
                    <input
                      value={draft}
                      onChange={(event) => setDraft(event.target.value)}
                      placeholder="Write a message"
                      aria-label="Message"
                      maxLength={4000}
                    />
                    <button className="button" type="submit" disabled={busy || !draft.trim()}>
                      Send
                    </button>
                  </form>
                </>
              ) : (
                <div className="messenger-list">
                  {threads.length === 0 ? (
                    <p className="messenger-empty">
                      No conversations yet. Open one from a model page.
                    </p>
                  ) : threads.map((thread) => (
                    <button
                      key={thread.thread_id}
                      type="button"
                      className="messenger-row"
                      onClick={() => void openThread(thread.thread_id)}
                    >
                      <span className="messenger-row-title">
                        {thread.listing_title || "Untitled model"}
                        {thread.unread > 0 ? (
                          <span className="messenger-unread">{thread.unread}</span>
                        ) : null}
                      </span>
                      <span className="messenger-row-preview">
                        {thread.last_message ?? "No messages yet"}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </section>
          ) : null}

          <button
            className="messenger-launcher"
            type="button"
            aria-label={unread ? `Messages, ${unread} unread` : "Messages"}
            aria-expanded={open}
            onClick={() => setOpen((current) => !current)}
          >
            <span aria-hidden="true">💬</span>
            {unread > 0 ? <span className="messenger-badge">{unread}</span> : null}
          </button>
        </div>
      ) : null}
    </MessengerContext.Provider>
  );
}

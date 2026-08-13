import { useEffect, useRef, useState } from "react";
import { apiFetch } from "../api/client";
import { useToast } from "./Toast";
import { BrainLogo } from "./icons";
import type { AskResponse, ChatTurn } from "../api/types";

export function AskPanel() {
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [lastSources, setLastSources] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const { pushError } = useToast();
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [turns, loading]);

  const ask = async (text?: string) => {
    const trimmed = (text ?? question).trim();
    if (!trimmed || loading) return;
    setLoading(true);
    setTurns((prev) => [...prev, { role: "user", content: trimmed }]);
    setQuestion("");
    try {
      const history = turns.map((t) => ({ role: t.role, content: t.content }));
      const result = await apiFetch<AskResponse>(
        "/ask",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: trimmed, history }),
        },
        90_000,
      );
      setTurns((prev) => [...prev, { role: "assistant", content: result.answer }]);
      setLastSources(result.sources);
    } catch (err) {
      setTurns((prev) => [
        ...prev,
        { role: "assistant", content: "(keine Antwort erhalten)" },
      ]);
      pushError(err, "Frage konnte nicht beantwortet werden");
    } finally {
      setLoading(false);
    }
  };

  const clear = () => {
    setTurns([]);
    setLastSources([]);
  };

  return (
    <div className="chat-tab">
      <div className="chat-header">
        <h2>Chat</h2>
        {turns.length > 0 && (
          <button className="chat-clear" onClick={clear}>
            Verlauf löschen
          </button>
        )}
      </div>

      <div className="chat-thread" ref={scrollRef}>
        {turns.length === 0 && !loading && (
          <div className="chat-empty">
            <p>Stell CBKS eine Frage zu deinem Wissensgraphen, zum Beispiel:</p>
            <div className="chat-suggestions">
              {[
                "Was sind meine wiederkehrenden Themen?",
                "Welche Widersprüche gibt es in meinen Notizen?",
                "Fasse meine letzten Notizen zusammen",
              ].map((q) => (
                <button key={q} type="button" className="chat-suggestion" onClick={() => ask(q)}>
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}
        {turns.map((turn, i) => (
          <div key={i} className="chat-msg">
            <div className={`chat-avatar chat-avatar-${turn.role}`}>
              {turn.role === "user" ? "Du" : <BrainLogo size={14} />}
            </div>
            <div className="chat-msg-body">
              <span className="chat-msg-role">{turn.role === "user" ? "Du" : "CBKS"}</span>
              <p className="chat-msg-content">{turn.content}</p>
              {turn.role === "assistant" && i === turns.length - 1 && lastSources.length > 0 && (
                <ul className="chat-sources">
                  {lastSources.map((source) => (
                    <li key={source} title={source}>
                      {source.slice(0, 8)}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        ))}
        {loading && (
          <div className="chat-msg">
            <div className="chat-avatar chat-avatar-assistant">
              <BrainLogo size={14} />
            </div>
            <div className="chat-msg-body">
              <span className="chat-msg-role">CBKS</span>
              <p className="chat-msg-content chat-msg-loading">denkt nach…</p>
            </div>
          </div>
        )}
      </div>

      <div className="chat-input-row">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && ask()}
          placeholder="Frage an CBKS stellen..."
          disabled={loading}
        />
        <button className="chat-send" onClick={() => ask()} disabled={loading || !question.trim()}>
          {loading ? "…" : "Senden"}
        </button>
      </div>
    </div>
  );
}

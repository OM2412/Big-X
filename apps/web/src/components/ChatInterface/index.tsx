"use client";

import { useState, useEffect } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

export default function ChatInterface() {
  const [token, setToken] = useState<string | null>(null);
  const [messages, setMessages] = useState<Array<{ role: string; content: string }>>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedAgent, setSelectedAgent] = useState("default");

  useEffect(() => {
    if (typeof window !== "undefined") {
      setToken(localStorage.getItem("access_token"));
    }
  }, []);

  async function sendMessage(e: React.FormEvent) {
    e.preventDefault();
    if (!input.trim() || !token) {
      setError("Please connect your wallet first");
      return;
    }

    const userMessage = { role: "user", content: input };
    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setLoading(true);
    setError(null);

    try {
      const res = await fetch(`${API_BASE}/api/v1/execute`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          agent_id: selectedAgent,
          message: input,
          metadata: {},
        }),
      });

      if (res.status === 401) {
        if (typeof window !== "undefined") {
          localStorage.removeItem("access_token");
          localStorage.removeItem("wallet_address");
        }
        setToken(null);
        throw new Error("Session expired. Please reconnect your wallet.");
      }

      if (!res.ok) {
        const text = await res.text();
        throw new Error(`Chat failed: ${res.status} ${text}`);
      }

      const data = await res.json();
      const reply = data.message || data.reply || "Task started";
      setMessages((prev) => [...prev, { role: "assistant", content: reply }]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to send message");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="chat-interface">
      <h2>Chat Interface</h2>
      <div className="chat-controls">
        <label>
          Agent:
          <select value={selectedAgent} onChange={(e) => setSelectedAgent(e.target.value)}>
            <option value="default">Default Agent</option>
            <option value="orchestrator">Orchestrator</option>
          </select>
        </label>
      </div>
      <div className="chat-messages">
        {messages.length === 0 && <p>Send a message to start a task.</p>}
        {messages.map((msg, i) => (
          <div key={i} className={`chat-message ${msg.role}`}>
            <strong>{msg.role === "user" ? "You" : "Agent"}:</strong> {msg.content}
          </div>
        ))}
        {loading && <div className="chat-message assistant">Thinking...</div>}
        {error && <div className="chat-message error">Error: {error}</div>}
      </div>
      <form onSubmit={sendMessage} className="chat-form">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Type a message..."
          disabled={loading || !token}
        />
        <button type="submit" disabled={loading || !input.trim() || !token}>
          Send
        </button>
      </form>
    </div>
  );
}

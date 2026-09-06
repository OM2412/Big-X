"use client";

import { useEffect, useState } from "react";
import { useAuth } from "../../hooks";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

interface Agent {
  id: string;
  name: string;
  persona: string | null;
  model_version: string;
  metadata_uri: string | null;
  endpoint: string | null;
  capabilities: number;
  state: string;
  nft_id: number | null;
  token_bound_account: string | null;
  creator_wallet: string;
}

export default function AgentStudio() {
  const { token } = useAuth();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formData, setFormData] = useState({
    name: "",
    persona: "",
    model_version: "default",
    metadata_uri: "",
    endpoint: "",
  });

  const fetchAgents = () => {
    if (!token) {
      setLoading(false);
      setError("Please connect your wallet to view agents");
      return;
    }
    setError(null);
    setLoading(true);
    fetch(`${API_BASE}/v1/studio/agents`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then(async (res) => {
        if (res.status === 401) {
          localStorage.removeItem("access_token");
          localStorage.removeItem("wallet_address");
          window.location.reload();
          return;
        }
        if (!res.ok) {
          const text = await res.text();
          throw new Error(`Agents fetch failed: ${res.status} ${text}`);
        }
        return res.json();
      })
      .then((data) => {
        setAgents(data || []);
      })
      .catch((err) => {
        console.error("Studio API Error:", err);
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    fetchAgents();
  }, [token]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token) return;
    try {
      const res = await fetch(`${API_BASE}/v1/studio/agents`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(formData),
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`Create failed: ${res.status} ${text}`);
      }
      setShowForm(false);
      setFormData({ name: "", persona: "", model_version: "default", metadata_uri: "", endpoint: "" });
      fetchAgents();
    } catch (err) {
      console.error("Create agent API Error:", err);
      alert(err instanceof Error ? err.message : "Failed to create agent");
    }
  };

  const handleUpdate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token || !editingId) return;
    try {
      const res = await fetch(`${API_BASE}/v1/studio/agents/${editingId}`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(formData),
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`Update failed: ${res.status} ${text}`);
      }
      setEditingId(null);
      setFormData({ name: "", persona: "", model_version: "default", metadata_uri: "", endpoint: "" });
      fetchAgents();
    } catch (err) {
      console.error("Update agent API Error:", err);
      alert(err instanceof Error ? err.message : "Failed to update agent");
    }
  };

  const handlePublish = async (agentId: string) => {
    if (!token) return;
    try {
      const res = await fetch(`${API_BASE}/v1/studio/agents/${agentId}/publish`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`Publish failed: ${res.status} ${text}`);
      }
      fetchAgents();
    } catch (err) {
      console.error("Publish API Error:", err);
      alert(err instanceof Error ? err.message : "Failed to publish agent");
    }
  };

  const handleUnpublish = async (agentId: string) => {
    if (!token) return;
    try {
      const res = await fetch(`${API_BASE}/v1/studio/agents/${agentId}/unpublish`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`Unpublish failed: ${res.status} ${text}`);
      }
      fetchAgents();
    } catch (err) {
      console.error("Unpublish API Error:", err);
      alert(err instanceof Error ? err.message : "Failed to unpublish agent");
    }
  };

  if (loading) return <div className="agent-studio">Loading agents...</div>;
  if (error) {
    return (
      <div className="agent-studio">
        <h2>Agent Studio</h2>
        <p className="error-text">{error}</p>
        {!token && (
          <button onClick={() => window.location.reload()} className="retry-button">
            Reconnect Wallet
          </button>
        )}
      </div>
    );
  }

  const handleEdit = (agent: Agent) => {
    setEditingId(agent.id);
    setFormData({
      name: agent.name,
      persona: agent.persona || "",
      model_version: agent.model_version,
      metadata_uri: agent.metadata_uri || "",
      endpoint: agent.endpoint || "",
    });
    setShowForm(true);
  };

  const handleCancelForm = () => {
    setShowForm(false);
    setEditingId(null);
    setFormData({ name: "", persona: "", model_version: "default", metadata_uri: "", endpoint: "" });
  };

  return (
    <div className="agent-studio">
      <h2>Agent Studio</h2>
      {!showForm && (
        <button className="create-button" onClick={() => setShowForm(true)}>
          Create New Agent
        </button>
      )}
      {showForm && (
        <form onSubmit={editingId ? handleUpdate : handleCreate} className="agent-form">
          <input
            type="text"
            placeholder="Agent Name"
            value={formData.name}
            onChange={(e) => setFormData({ ...formData, name: e.target.value })}
            required
          />
          <textarea
            placeholder="Persona"
            value={formData.persona}
            onChange={(e) => setFormData({ ...formData, persona: e.target.value })}
          />
          <input
            type="text"
            placeholder="Model Version"
            value={formData.model_version}
            onChange={(e) => setFormData({ ...formData, model_version: e.target.value })}
          />
          <input
            type="text"
            placeholder="Metadata URI (IPFS/Arweave)"
            value={formData.metadata_uri}
            onChange={(e) => setFormData({ ...formData, metadata_uri: e.target.value })}
          />
          <input
            type="text"
            placeholder="Endpoint URL"
            value={formData.endpoint}
            onChange={(e) => setFormData({ ...formData, endpoint: e.target.value })}
          />
          <div className="form-actions">
            <button type="submit">{editingId ? "Update Agent" : "Create Agent"}</button>
            <button type="button" onClick={handleCancelForm}>Cancel</button>
          </div>
        </form>
      )}
      {agents.length === 0 ? (
        <p>No agents yet. Create your first agent above.</p>
      ) : (
        <div className="agents-grid">
          {agents.map((agent) => (
            <div key={agent.id} className="agent-card">
              <h3>{agent.name}</h3>
              <p>State: {agent.state}</p>
              {agent.persona && <p>Persona: {agent.persona}</p>}
              <p>Model: {agent.model_version}</p>
              {agent.nft_id && <p>NFT ID: #{agent.nft_id}</p>}
              {agent.token_bound_account && (
                <p>Wallet: {agent.token_bound_account.slice(0, 6)}...{agent.token_bound_account.slice(-4)}</p>
              )}
              <div className="agent-actions">
                <button className="edit-button" onClick={() => handleEdit(agent)}>Edit</button>
                {agent.state !== "active" && (
                  <button className="publish-button" onClick={() => handlePublish(agent.id)}>Publish</button>
                )}
                {agent.state === "active" && (
                  <button className="unpublish-button" onClick={() => handleUnpublish(agent.id)}>Unpublish</button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

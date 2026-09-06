"use client";

import { useEffect, useState } from "react";
import { api, AgentSummary, ApiError } from "./Api";
import { useTaskPolling } from "./UsetaskPolling";

const STATE_LABELS: Record<AgentSummary["state"], string> = {
  created: "Created",
  provisioning: "Provisioning",
  active: "Active",
  suspended: "Suspended",
  deprecated: "Deprecated",
  archived: "Archived",
};

const STATE_COLORS: Record<AgentSummary["state"], string> = {
  created: "#888",
  provisioning: "#e0a020",
  active: "#2ecc71",
  suspended: "#e74c3c",
  deprecated: "#888",
  archived: "#555",
};

export default function AgentsList() {
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshingId, setRefreshingId] = useState<string | null>(null);

  const { status: taskStatus, isPolling, startPolling } = useTaskPolling();

  const loadAgents = () => {
    setLoading(true);
    api.agents
      .list()
      .then(setAgents)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Failed to load agents"))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadAgents();
  }, []);

  // Once the background refresh task completes, reload the agent list so
  // the newly-synced state/capabilities from the chain actually show up.
  useEffect(() => {
    if (taskStatus?.status === "SUCCESS" && refreshingId) {
      loadAgents();
      setRefreshingId(null);
    }
  }, [taskStatus, refreshingId]);

  async function handleRefresh(agentId: string) {
    setRefreshingId(agentId);
    try {
      const { task_id } = await api.agents.refresh(agentId);
      startPolling(task_id);
    } catch (err) {
      setRefreshingId(null);
      setError(err instanceof ApiError ? err.message : "Failed to start refresh");
    }
  }

  if (loading) return <div>Loading agents...</div>;
  if (error) return <div>Error: {error}</div>;

  return (
    <div className="agents-list">
      <h2>Your Agents</h2>

      {agents.length === 0 ? (
        <p>No agents yet — mint one to get started.</p>
      ) : (
        <div className="agents-grid">
          {agents.map((agent) => {
            const isThisAgentRefreshing = refreshingId === agent.id && isPolling;

            return (
              <div key={agent.id} className="agent-card">
                <div className="agent-card-header">
                  <h3>{agent.name}</h3>
                  <span className="agent-state" style={{ color: STATE_COLORS[agent.state] }}>
                    {STATE_LABELS[agent.state]}
                  </span>
                </div>

                <p className="agent-nft-id">NFT #{agent.nft_id}</p>
                {agent.token_bound_account && (
                  <p className="agent-tba">
                    Wallet: {agent.token_bound_account.slice(0, 6)}...{agent.token_bound_account.slice(-4)}
                  </p>
                )}

                <button onClick={() => handleRefresh(agent.id)} disabled={isThisAgentRefreshing}>
                  {isThisAgentRefreshing ? "Syncing from chain..." : "Refresh from chain"}
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

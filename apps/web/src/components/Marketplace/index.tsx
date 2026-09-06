"use client";

import { useEffect, useState } from "react";
import { useAuth } from "../../hooks";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

interface MarketplaceAgent {
  id: string;
  name: string;
  persona: string | null;
  model_version: string;
  price: number | null;
  currency: string;
  creator_wallet: string;
  nft_id: number | null;
  state: string;
  metadata_uri: string | null;
}

export default function Marketplace() {
  const { token } = useAuth();
  const [agents, setAgents] = useState<MarketplaceAgent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [filterState, setFilterState] = useState<string>("all");
  const [buyingId, setBuyingId] = useState<string | null>(null);

  useEffect(() => {
    if (!token) {
      setLoading(false);
      setError("Please connect your wallet to view marketplace");
      return;
    }

    setError(null);
    setLoading(true);
    fetch(`${API_BASE}/v1/marketplace/agents`, {
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
          throw new Error(`Marketplace fetch failed: ${res.status} ${text}`);
        }
        return res.json();
      })
      .then((data) => {
        setAgents(data || []);
      })
      .catch((err) => {
        console.error("Marketplace API Error:", err);
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => setLoading(false));
  }, [token]);

  const handleBuy = async (agentId: string) => {
    if (!token) return;
    setBuyingId(agentId);
    try {
      const res = await fetch(`${API_BASE}/v1/buy`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ agent_id: agentId, payment_method: "crypto" }),
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`Buy failed: ${res.status} ${text}`);
      }
      const data = await res.json();
      alert(`Purchase initiated: ${data.message}`);
    } catch (err) {
      console.error("Buy API Error:", err);
      alert(err instanceof Error ? err.message : "Failed to purchase");
    } finally {
      setBuyingId(null);
    }
  };

  const filtered = agents.filter((a) => {
    const matchesSearch = a.name.toLowerCase().includes(search.toLowerCase());
    const matchesFilter = filterState === "all" || a.state === filterState;
    return matchesSearch && matchesFilter;
  });

  if (loading) return <div className="marketplace">Loading marketplace...</div>;
  if (error) {
    return (
      <div className="marketplace">
        <h2>Marketplace</h2>
        <p className="error-text">{error}</p>
        {!token && (
          <button onClick={() => window.location.reload()} className="retry-button">
            Reconnect Wallet
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="marketplace">
      <h2>Marketplace</h2>
      <div className="marketplace-controls">
        <input
          type="text"
          placeholder="Search agents..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="search-input"
        />
        <select value={filterState} onChange={(e) => setFilterState(e.target.value)} className="filter-select">
          <option value="all">All States</option>
          <option value="active">Active</option>
          <option value="created">Created</option>
          <option value="suspended">Suspended</option>
        </select>
      </div>
      {filtered.length === 0 ? (
        <p>No agents found.</p>
      ) : (
        <div className="agents-grid">
          {filtered.map((agent) => (
            <div key={agent.id} className="agent-card">
              <h3>{agent.name}</h3>
              <p>State: {agent.state}</p>
              <p>Model: {agent.model_version}</p>
              {agent.persona && <p>Persona: {agent.persona}</p>}
              {agent.nft_id && <p>NFT ID: #{agent.nft_id}</p>}
              {agent.price !== null && <p>Price: {agent.price} {agent.currency}</p>}
              <p>Creator: {agent.creator_wallet.slice(0, 6)}...{agent.creator_wallet.slice(-4)}</p>
              <div className="agent-actions">
                <button className="buy-button" onClick={() => handleBuy(agent.id)} disabled={buyingId === agent.id}>
                  {buyingId === agent.id ? "Processing..." : "Buy"}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

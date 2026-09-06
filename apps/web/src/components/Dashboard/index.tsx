"use client";

import { useEffect, useState } from "react";
import { useAuth } from "../../hooks";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

interface Position {
  agent_name: string;
  asset: string;
  amount: number;
  value_usd: number;
}

interface PortfolioResponse {
  total_value_usd: number;
  positions: Position[];
}

interface PurchasedAgent {
  id: string;
  name: string;
  state: string;
  nft_id: number | null;
}

interface ActivityItem {
  id: string;
  type: string;
  description: string;
  timestamp: string;
}

export default function Dashboard({ onNavigate }: { onNavigate?: (id: string) => void }) {
  const { token } = useAuth();
  const [data, setData] = useState<PortfolioResponse | null>(null);
  const [purchased, setPurchased] = useState<PurchasedAgent[]>([]);
  const [activities, setActivities] = useState<ActivityItem[]>([]);
  const [revenue, setRevenue] = useState<number>(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) {
      setLoading(false);
      setError("Please connect your wallet to view portfolio");
      return;
    }

    setError(null);
    setLoading(true);

    const headers = { Authorization: `Bearer ${token}` };
    Promise.all([
      fetch(`${API_BASE}/v1/portfolio`, { headers }),
      fetch(`${API_BASE}/v1/marketplace/my-purchases`, { headers }),
      fetch(`${API_BASE}/v1/studio/agents`, { headers }),
    ])
      .then(async ([portfolioRes, purchasesRes, studioRes]) => {
        if (portfolioRes.status === 401 || purchasesRes.status === 401 || studioRes.status === 401) {
          localStorage.removeItem("access_token");
          localStorage.removeItem("wallet_address");
          window.location.reload();
          return;
        }
        if (!portfolioRes.ok) {
          const text = await portfolioRes.text();
          throw new Error(`Portfolio fetch failed: ${portfolioRes.status} ${text}`);
        }
        if (!purchasesRes.ok) {
          const text = await purchasesRes.text();
          throw new Error(`Purchases fetch failed: ${purchasesRes.status} ${text}`);
        }
        if (!studioRes.ok) {
          const text = await studioRes.text();
          throw new Error(`Studio fetch failed: ${studioRes.status} ${text}`);
        }

        const portfolioJson = await portfolioRes.json();
        const purchasesJson = await purchasesRes.json();
        const studioJson = await studioRes.json();

        setData(portfolioJson);

        const purchasedAgents = (purchasesJson || []).map((a: any) => ({
          id: a.id,
          name: a.name,
          state: a.state,
          nft_id: a.nft_id,
        }));
        setPurchased(purchasedAgents);

        const publishedAgents = (studioJson || []).filter((a: any) => a.state === "active");
        setRevenue(publishedAgents.length * 0.05);

        // The portfolio endpoints do not include event timestamps. Keep the
        // activity surface honest until the indexed event feed is connected.
        setActivities([]);
      })
      .catch((err) => {
        console.error("Dashboard API Error:", err);
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => setLoading(false));
  }, [token]);

  if (loading) return <div className="dashboard">Loading dashboard...</div>;
  if (error) {
    return (
      <div className="dashboard">
        <h2>Dashboard</h2>
        <p className="error-text">{error}</p>
        {!token && (
          <button onClick={() => window.location.reload()} className="retry-button">
            Reconnect Wallet
          </button>
        )}
      </div>
    );
  }

  const totalValue = data ? data.total_value_usd.toFixed(2) : "0.00";

  return (
    <div className="dashboard">
      <h2>Dashboard</h2>
      <div className="dashboard-grid">
        <div className="card">
          <h3>Portfolio Value</h3>
          <p>${totalValue} USD</p>
        </div>
        <div className="card">
          <h3>Positions</h3>
          {data && data.positions.length === 0 ? (
            <p>No positions</p>
          ) : (
            <ul>
              {data?.positions.map((p, i) => (
                <li key={i}>
                  {p.agent_name}: {p.amount} {p.asset} (${p.value_usd.toFixed(2)})
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="card">
          <h3>Revenue (est.)</h3>
          <p>{revenue.toFixed(4)} ETH</p>
        </div>
        <div className="card">
          <h3>Purchased Agents</h3>
          {purchased.length === 0 ? (
            <p>No purchases yet</p>
          ) : (
            <ul>
              {purchased.map((a) => (
                <li key={a.id}>
                  {a.name} {a.nft_id ? `#${a.nft_id}` : ""}
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="card full-width">
          <h3>Recent Activities</h3>
          {activities.length === 0 ? (
            <p>No recent activities</p>
          ) : (
            <ul>
              {activities.map((act) => (
                <li key={act.id}>
                  <strong>{act.type}:</strong> {act.description} - {new Date(act.timestamp).toLocaleString()}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

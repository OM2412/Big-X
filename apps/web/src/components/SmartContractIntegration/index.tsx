"use client";

import { useEffect, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

interface Transaction {
  id: string;
  tx_hash: string | null;
  tx_type: string;
  status: string;
  chain_id: number;
  amount: number | null;
  confirmed_at: string | null;
}

export default function SmartContractIntegration() {
  const [token, setToken] = useState<string | null>(null);
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedAgent, setSelectedAgent] = useState("");
  const [toAddress, setToAddress] = useState("");
  const [transferStatus, setTransferStatus] = useState<string | null>(null);
  const [listPrice, setListPrice] = useState("");
  const [listStatus, setListStatus] = useState<string | null>(null);

  useEffect(() => {
    if (typeof window !== "undefined") {
      setToken(localStorage.getItem("access_token"));
    }
  }, []);

  const fetchTransactions = () => {
    if (!token || !selectedAgent) {
      setTransactions([]);
      setLoading(false);
      return;
    }
    fetch(`${API_BASE}/v1/smart-contracts/transactions/${selectedAgent}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((res) => {
        if (res.status === 401) {
          if (typeof window !== "undefined") {
            localStorage.removeItem("access_token");
            localStorage.removeItem("wallet_address");
          }
          setToken(null);
          throw new Error("Session expired. Please reconnect your wallet.");
        }
        if (!res.ok) throw new Error(`Failed to fetch transactions: ${res.status}`);
        return res.json();
      })
      .then(setTransactions)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    fetchTransactions();
  }, [token, selectedAgent]);

  const handleTransfer = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token || !selectedAgent) return;
    setTransferStatus("Initiating transfer...");
    try {
      const res = await fetch(`${API_BASE}/v1/smart-contracts/transfer`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ agent_id: selectedAgent, to_address: toAddress }),
      });
      if (!res.ok) throw new Error(`Transfer failed: ${res.status}`);
      const data = await res.json();
      setTransferStatus(data.message);
    } catch (err) {
      setTransferStatus(err instanceof Error ? err.message : "Transfer failed");
    }
  };

  const handleList = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token || !selectedAgent) return;
    setListStatus("Listing agent...");
    try {
      const res = await fetch(`${API_BASE}/v1/smart-contracts/list`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ agent_id: selectedAgent, price_eth: parseFloat(listPrice) }),
      });
      if (!res.ok) throw new Error(`List failed: ${res.status}`);
      const data = await res.json();
      setListStatus(data.message);
    } catch (err) {
      setListStatus(err instanceof Error ? err.message : "List failed");
    }
  };

  if (loading) return <div className="smart-contract">Loading smart contract data...</div>;
  if (error) {
    return (
      <div className="smart-contract">
        <h2>Smart Contract Integration</h2>
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
    <div className="smart-contract">
      <h2>Smart Contract Integration</h2>
      <div className="contract-controls">
        <label>
          Agent ID:
          <input
            type="text"
            value={selectedAgent}
            onChange={(e) => setSelectedAgent(e.target.value)}
            placeholder="Enter agent ID to view transactions"
          />
        </label>
      </div>
      <div className="contract-actions">
        <form onSubmit={handleTransfer} className="contract-form">
          <h3>Transfer Agent</h3>
          <input
            type="text"
            placeholder="To Address"
            value={toAddress}
            onChange={(e) => setToAddress(e.target.value)}
          />
          <button type="submit" disabled={!toAddress}>Transfer</button>
          {transferStatus && <p>{transferStatus}</p>}
        </form>
        <form onSubmit={handleList} className="contract-form">
          <h3>List for Sale</h3>
          <input
            type="number"
            step="0.001"
            placeholder="Price (ETH)"
            value={listPrice}
            onChange={(e) => setListPrice(e.target.value)}
          />
          <button type="submit" disabled={!listPrice}>List</button>
          {listStatus && <p>{listStatus}</p>}
        </form>
      </div>
      <h3>Transaction History</h3>
      {transactions.length === 0 ? (
        <p>No transactions found for this agent.</p>
      ) : (
        <table className="tx-table">
          <thead>
            <tr>
              <th>Type</th>
              <th>Status</th>
              <th>Chain</th>
              <th>Amount</th>
              <th>Tx Hash</th>
              <th>Confirmed</th>
            </tr>
          </thead>
          <tbody>
            {transactions.map((tx) => (
              <tr key={tx.id}>
                <td>{tx.tx_type}</td>
                <td>{tx.status}</td>
                <td>{tx.chain_id}</td>
                <td>{tx.amount ?? "-"}</td>
                <td>{tx.tx_hash ? `${tx.tx_hash.slice(0, 10)}...` : "-"}</td>
                <td>{tx.confirmed_at ? new Date(tx.confirmed_at).toLocaleString() : "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

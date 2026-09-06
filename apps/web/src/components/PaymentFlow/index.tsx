"use client";

import { useEffect, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

export default function PaymentFlow() {
  const [token, setToken] = useState<string | null>(null);
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState("ETH");
  const [provider, setProvider] = useState("crypto");
  const [agentId, setAgentId] = useState("");
  const [paymentStatus, setPaymentStatus] = useState<string | null>(null);
  const [paymentId, setPaymentId] = useState<string | null>(null);
  const [confirmHash, setConfirmHash] = useState("");
  const [confirmStatus, setConfirmStatus] = useState<string | null>(null);

  useEffect(() => {
    if (typeof window !== "undefined") {
      setToken(localStorage.getItem("access_token"));
    }
  }, []);

  const createPaymentIntent = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token) return;
    setPaymentStatus("Creating payment intent...");
    try {
      const endpoint = provider === "fiat" ? "/v1/payments/fiat" : "/v1/payments/intent";
      const body: any = {
        amount: parseFloat(amount),
        currency,
        provider,
      };
      if (agentId) body.agent_id = agentId;

      const res = await fetch(`${API_BASE}${endpoint}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`Payment intent failed: ${res.status}`);
      const data = await res.json();
      setPaymentId(data.payment_id);
      setPaymentStatus(`Payment intent created: ${data.message}`);
    } catch (err) {
      setPaymentStatus(err instanceof Error ? err.message : "Failed to create payment intent");
    }
  };

  const confirmPayment = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token || !paymentId) return;
    setConfirmStatus("Confirming payment...");
    try {
      const res = await fetch(`${API_BASE}/v1/payments/confirm`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ payment_id: paymentId, tx_hash: confirmHash }),
      });
      if (!res.ok) throw new Error(`Confirm failed: ${res.status}`);
      const data = await res.json();
      setConfirmStatus(`Payment confirmed: ${data.status}`);
    } catch (err) {
      setConfirmStatus(err instanceof Error ? err.message : "Failed to confirm payment");
    }
  };

  return (
    <div className="payment-flow">
      <h2>Payment Flow</h2>
      <div className="payment-section">
        <h3>Create Payment Intent</h3>
        <form onSubmit={createPaymentIntent} className="payment-form">
          <label>
            Provider:
            <select value={provider} onChange={(e) => setProvider(e.target.value)}>
              <option value="crypto">Crypto (MetaMask)</option>
              <option value="fiat">Fiat (Stripe/Razorpay)</option>
            </select>
          </label>
          <label>
            Amount:
            <input
              type="number"
              step="0.001"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              placeholder="0.1"
              required
            />
          </label>
          <label>
            Currency:
            <select value={currency} onChange={(e) => setCurrency(e.target.value)}>
              <option value="ETH">ETH</option>
              <option value="USD">USD</option>
            </select>
          </label>
          <label>
            Agent ID (optional):
            <input
              type="text"
              value={agentId}
              onChange={(e) => setAgentId(e.target.value)}
              placeholder="Agent ID"
            />
          </label>
          <button type="submit" disabled={!token}>Create Payment Intent</button>
          {paymentStatus && <p>{paymentStatus}</p>}
        </form>
      </div>
      {paymentId && (
        <div className="payment-section">
          <h3>Confirm Payment</h3>
          <form onSubmit={confirmPayment} className="payment-form">
            <label>
              Transaction Hash:
              <input
                type="text"
                value={confirmHash}
                onChange={(e) => setConfirmHash(e.target.value)}
                placeholder="0x..."
                required
              />
            </label>
            <button type="submit">Confirm Payment</button>
            {confirmStatus && <p>{confirmStatus}</p>}
          </form>
        </div>
      )}
    </div>
  );
}

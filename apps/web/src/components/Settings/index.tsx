"use client";

import { useState, useEffect } from "react";

export default function Settings() {
  const [address, setAddress] = useState<string | null>(null);

  useEffect(() => {
    if (typeof window !== "undefined") {
      const stored = localStorage.getItem("wallet_address");
      if (stored) {
        setAddress(stored);
      } else {
        // Fallback: try to get connected address from MetaMask
        const ethereum = (window as any).ethereum;
        if (ethereum) {
          ethereum.request({ method: "eth_accounts" }).then((accounts: string[]) => {
            if (accounts.length > 0) {
              setAddress(accounts[0]);
            }
          });
        }
      }
    }
  }, []);

  function disconnect() {
    if (typeof window !== "undefined") {
      localStorage.removeItem("access_token");
      localStorage.removeItem("wallet_address");
    }
    setAddress(null);
    window.location.reload();
  }

  return (
    <div className="settings">
      <h2>Settings</h2>
      <div className="settings-section">
        <h3>Wallet</h3>
        {address ? (
          <div className="setting-item">
            <span>Connected: {address.slice(0, 6)}...{address.slice(-4)}</span>
            <button onClick={disconnect} className="disconnect-button">
              Disconnect Wallet
            </button>
          </div>
        ) : (
          <p>No wallet connected</p>
        )}
      </div>
      <div className="settings-section">
        <h3>Network</h3>
        <div className="setting-item">
          <span>Chain ID: 31337 (Anvil / Localhost)</span>
        </div>
      </div>
      <div className="settings-section">
        <h3>About</h3>
        <p>Agentic DeFi Platform - Backend Service Layer</p>
        <p>Version: 0.1.0</p>
      </div>
    </div>
  );
}

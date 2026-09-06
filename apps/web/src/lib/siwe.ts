"use client";

// lib/siwe.ts
//
// Builds a SIWE (Sign-in with Ethereum) message, has the wallet sign it,
// and exchanges the signature for a session token from the API gateway.

import { connectWallet, signMessage } from "./wallet-connect";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

function buildSiweMessage(address: string, nonce: string): string {
  const domain = typeof window !== "undefined" ? window.location.host : "localhost";
  const uri = typeof window !== "undefined" ? window.location.origin : "http://localhost";
  const issuedAt = new Date().toISOString();

  return `${domain} wants you to sign in with your Ethereum account:
${address}

Sign in to Agentic DeFi Platform.

URI: ${uri}
Version: 1
Chain ID: 1
Nonce: ${nonce}
Issued At: ${issuedAt}`;
}

async function fetchNonce(address: string): Promise<string> {
  const url = `${API_BASE}/v1/auth/nonce?address=${encodeURIComponent(address)}`;
  console.log("[siwe] Fetching nonce from:", url);
  try {
    const res = await fetch(url);
    console.log("[siwe] Nonce response:", res.status, res.statusText);
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Failed to fetch nonce: ${res.status} ${text}`);
    }
    const data = await res.json();
    return data.nonce;
  } catch (err) {
    console.error("[siwe] Nonce fetch failed:", err);
    if (err instanceof TypeError && err.message === "Failed to fetch") {
      throw new Error("Network error: Cannot reach backend. Make sure the backend server is running on " + API_BASE);
    }
    throw err;
  }
}

export async function loginWithSiwe(): Promise<string> {
  const address = await connectWallet();
  if (!address) {
    throw new Error("Wallet connection was cancelled or no wallet detected.");
  }
  const nonce = await fetchNonce(address);
  const message = buildSiweMessage(address, nonce);
  const signature = await signMessage(address, message);

  const res = await fetch(`${API_BASE}/v1/auth/siwe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, signature }),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`SIWE login failed: ${res.status} ${text}`);
  }

  const data = await res.json();
  if (typeof window !== "undefined") {
    localStorage.setItem("access_token", data.access_token);
    localStorage.setItem("wallet_address", address);
  }
  return data.access_token;
}

export function logout(): void {
  if (typeof window !== "undefined") {
    localStorage.removeItem("access_token");
    localStorage.removeItem("wallet_address");
  }
}
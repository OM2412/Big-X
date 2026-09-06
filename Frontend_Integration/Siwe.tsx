"use client";

import { connectWallet, signMessage } from "./wallet-connect";
import { api } from "./Api";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

function buildSiweMessage(
  address: string,
  nonce: string,
  chainId: number,
): string {
  const domain = typeof window !== "undefined" ? window.location.host : "localhost";
  const uri = typeof window !== "undefined" ? window.location.origin : "http://localhost";
  const issuedAt = new Date().toISOString();

  return `${domain} wants you to sign in with your Ethereum account:
${address}

Sign in to Agentic DeFi Platform.

URI: ${uri}
Version: 1
Chain ID: ${chainId}
Nonce: ${nonce}
Issued At: ${issuedAt}`;
}

export async function loginWithSiwe(): Promise<string> {
  const address = await connectWallet();

  const { nonce } = await api.auth.getNonce(address);

  const chainId = typeof window !== "undefined" && window.ethereum
    ? parseInt(window.ethereum.chainId, 16)
    : 1;

  const message = buildSiweMessage(address, nonce, chainId);

  const signature = await signMessage(address, message);

  const { access_token } = await api.auth.loginWithSiwe(
    message,
    signature,
  );

  if (typeof window !== "undefined") {
    localStorage.setItem("access_token", access_token);
  }

  return access_token;
}

export function logout(): void {
  if (typeof window !== "undefined") {
    localStorage.removeItem("access_token");
  }
}
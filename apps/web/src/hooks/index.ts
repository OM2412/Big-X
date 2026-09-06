// hooks/index.ts
//
// Shared React hooks. Keep adding hooks here as named exports rather
// than one-file-per-hook until this file actually gets unwieldy.

"use client";

import { useEffect, useState } from "react";
import { loginWithSiwe, logout } from "../lib/siwe";
import { getConnectedAddress } from "../lib/wallet-connect";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

export async function apiFetch(path: string, options: RequestInit = {}) {
  const token = typeof window !== "undefined" ? localStorage.getItem("access_token") : null;
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  headers.set("Content-Type", "application/json");

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });

  if (res.status === 401) {
    if (typeof window !== "undefined") {
      localStorage.removeItem("access_token");
      localStorage.removeItem("wallet_address");
    }
    window.location.reload();
    throw new Error("Session expired. Please reconnect your wallet.");
  }

  return res;
}

export function useAuth() {
  const [token, setToken] = useState<string | null>(null);
  const [address, setAddress] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (typeof window !== "undefined") {
      setToken(localStorage.getItem("access_token"));
      setAddress(localStorage.getItem("wallet_address"));
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    getConnectedAddress()
      .then((addr) => { if (addr) setAddress(addr); })
      .catch(() => {});
  }, []);

  async function login() {
    setLoading(true);
    setError(null);
    try {
      const newToken = await loginWithSiwe();
      if (typeof window !== "undefined") {
        localStorage.setItem("access_token", newToken);
      }
      setToken(newToken);
      setAddress(localStorage.getItem("wallet_address"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not connect. Please try again.");
      console.error("[useAuth] Login failed:", err);
    } finally {
      setLoading(false);
    }
  }

  function signOut() {
    if (typeof window !== "undefined") {
      localStorage.removeItem("access_token");
      localStorage.removeItem("wallet_address");
    }
    setToken(null);
    setAddress(null);
  }

  return { token, address, loading, error, isAuthenticated: !!token, login, signOut };
}

export function useApi<T>(path: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (typeof window === "undefined") return;

    const token = localStorage.getItem("access_token");

    fetch(`${API_BASE}${path}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then((res) => {
        if (res.status === 401) {
          if (typeof window !== "undefined") {
            localStorage.removeItem("access_token");
            localStorage.removeItem("wallet_address");
          }
          window.location.reload();
          throw new Error("Session expired. Please reconnect your wallet.");
        }
        if (!res.ok) throw new Error(`Request failed: ${res.status}`);
        return res.json();
      })
      .then(setData)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [path]);

  return { data, error, loading };
}

export { API_BASE };

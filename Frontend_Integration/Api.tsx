"use client";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public errorId: string,
    public path: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = typeof window !== "undefined" ? localStorage.getItem("access_token") : null;

  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });

  if (!response.ok) {
    let body: { error_id?: string; message?: string; path?: string };

    try {
      body = await response.json();
    } catch {
      body = {};
    }

    if (response.status === 401 && typeof window !== "undefined") {
      localStorage.removeItem("access_token");
    }

    throw new ApiError(
      body.message ?? `Request failed with status ${response.status}`,
      response.status,
      body.error_id ?? "unknown",
      body.path ?? path,
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json();
}

// -----------------------------------------------------------------------------
// Types matching backend Pydantic response models
// -----------------------------------------------------------------------------

export interface SessionUser {
  user_id: string;
  wallet_address: string | null;
  role: string;
}

export interface NonceResponse {
  nonce: string;
}

export interface AgentSummary {
  id: string;
  name: string;
  state: string;
  nft_id: number;
  persona: string | null;
  model_version: string;
  metadata_uri: string;
  endpoint: string | null;
  token_bound_account: string | null;
  capabilities: number;
  creator_wallet: string;
}

export interface MarketplaceAgent extends AgentSummary {
  price: number | null;
  seller: string | null;
}

export interface Position {
  agent_name: string;
  asset: string;
  amount: number;
  value_usd: number;
}

export interface Portfolio {
  total_value_usd: number;
  positions: Position[];
}

export interface DashboardData {
  wallet_balance: number;
  native_balance: string;
  published_agents: number;
  purchased_agents: number;
  revenue: number;
  recent_activities: Array<{
    id: string;
    type: string;
    status: string;
    amount_usd: number;
    tx_hash: string | null;
    created_at: string | null;
  }>;
}

export interface ChatMessage {
  task_id: string;
  status: string;
  reply: string;
  steps: Array<Record<string, unknown>>;
}

// -----------------------------------------------------------------------------
// API Client
// -----------------------------------------------------------------------------

export const api = {
  auth: {
    getNonce: (address: string) =>
      apiFetch<NonceResponse>(
        `/v1/auth/nonce?address=${encodeURIComponent(address)}`
      ),

    loginWithSiwe: (message: string, signature: string) =>
      apiFetch<{ access_token: string; token_type: string }>(
        "/v1/auth/siwe",
        {
          method: "POST",
          body: JSON.stringify({
            message,
            signature,
          }),
        }
      ),

    me: () => apiFetch<SessionUser>("/v1/me"),
  },

  dashboard: {
    get: () => apiFetch<DashboardData>("/v1/dashboard"),
  },

  marketplace: {
    list: (q?: string) =>
      apiFetch<MarketplaceAgent[]>(q ? `/v1/marketplace?q=${encodeURIComponent(q)}` : "/v1/marketplace"),

    get: (agentId: string) =>
      apiFetch<MarketplaceAgent>(`/v1/marketplace/${agentId}`),

    search: (q: string) =>
      apiFetch<{ query: string; results: AgentSummary[] }>(`/v1/search?q=${encodeURIComponent(q)}`),

    buy: (agentId: string, paymentMethod: "fiat" | "crypto", amount?: number) =>
      apiFetch<{ status: string; tx_hash: string }>(
        "/v1/buy",
        {
          method: "POST",
          body: JSON.stringify({
            agent_id: agentId,
            payment_method: paymentMethod,
            amount,
          }),
        }
      ),
  },

  agents: {
    list: () => apiFetch<{ agents: AgentSummary[] }>("/v1/agents"),

    get: (agentId: string) =>
      apiFetch<AgentSummary>(`/v1/agents/${agentId}`),

    create: (data: {
      name: string;
      persona?: string | null;
      model_version?: string;
      metadata_uri: string;
      endpoint?: string | null;
      capabilities?: number;
    }) =>
      apiFetch<{ id: string; name: string; state: string; message: string }>(
        "/v1/agents",
        {
          method: "POST",
          body: JSON.stringify(data),
        }
      ),

    update: (agentId: string, data: Record<string, unknown>) =>
      apiFetch<{ id: string; name: string; state: string }>(
        `/v1/agents/${agentId}`,
        {
          method: "PUT",
          body: JSON.stringify(data),
        }
      ),

    delete: (agentId: string) =>
      apiFetch<{ status: string; message: string }>(
        `/v1/agents/${agentId}`,
        {
          method: "DELETE",
        }
      ),

    myAgents: () => apiFetch<{ agents: AgentSummary[] }>("/v1/my-agents"),
  },

  chat: {
    send: (agentId: string, message: string) =>
      apiFetch<ChatMessage>("/v1/chat", {
        method: "POST",
        body: JSON.stringify({
          agent_id: agentId,
          message,
        }),
      }),
  },

  contracts: {
    transfer: (toAddress: string, nftId: number) =>
      apiFetch<{ status: string; tx_hash: string }>(
        "/v1/contracts/transfer",
        {
          method: "POST",
          body: JSON.stringify({
            to_address: toAddress,
            nft_id: nftId,
          }),
        }
      ),

    rent: (nftId: number, durationDays: number) =>
      apiFetch<{ status: string; tx_hash: string }>(
        "/v1/contracts/rent",
        {
          method: "POST",
          body: JSON.stringify({
            nft_id: nftId,
            duration_days: durationDays,
          }),
        }
      ),
  },

  payments: {
    crypto: (agentId: string, amount: number) =>
      apiFetch<{ status: string; tx_hash: string | null; message: string }>(
        "/v1/payments/crypto",
        {
          method: "POST",
          body: JSON.stringify({
            agent_id: agentId,
            amount,
          }),
        }
      ),

    fiat: (agentId: string, provider: "razorpay" | "stripe", amount: number, currency?: string) =>
      apiFetch<{ status: string; tx_hash: string | null; message: string }>(
        "/v1/payments/fiat",
        {
          method: "POST",
          body: JSON.stringify({
            agent_id: agentId,
            provider,
            amount,
            currency: currency || "usd",
          }),
        }
      ),
  },
};

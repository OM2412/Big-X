// lib/wallet-connect.ts
//
// Thin wrapper around browser wallet connection (MetaMask / injected provider).

declare global {
  interface Window {
    ethereum?: any;
  }
}

async function detectEthereum(retries = 10): Promise<any> {
  for (let i = 0; i < retries; i++) {
    if (typeof window !== "undefined" && window.ethereum) {
      return window.ethereum;
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  return typeof window !== "undefined" ? window.ethereum : undefined;
}

export async function connectWallet(): Promise<string | null> {
  if (typeof window === "undefined") {
    throw new Error("Browser environment not available.");
  }

  const ethereum = await detectEthereum();

  if (!ethereum) {
    alert("MetaMask not detected. Please install MetaMask and refresh the page.");
    return null;
  }

  try {
    const accounts = await ethereum.request({
      method: "eth_requestAccounts",
    });

    if (!accounts || accounts.length === 0) {
      alert("No accounts found. Please unlock MetaMask and try again.");
      return null;
    }

    return accounts[0];
  } catch (err: any) {
    if (err.code === 4001) {
      alert("Please approve the connection request in MetaMask.");
    } else if (err.code === -32000) {
      alert("MetaMask rejected the connection. Please unlock MetaMask and try again.");
    } else {
      console.error("[wallet-connect] eth_requestAccounts error:", err);
      alert("Wallet connection failed. See console for details.");
    }
    return null;
  }
}

export async function getConnectedAddress(): Promise<string | null> {
  if (typeof window === "undefined") return null;

  const ethereum = await detectEthereum(3);
  if (!ethereum) return null;

  try {
    const accounts: string[] = await ethereum.request({ method: "eth_accounts" });
    return accounts[0] ?? null;
  } catch {
    return null;
  }
}

export async function signMessage(address: string, message: string): Promise<string> {
  if (typeof window === "undefined") {
    throw new Error("Browser environment not available.");
  }

  const ethereum = await detectEthereum();
  if (!ethereum) {
    alert("MetaMask not detected. Please install MetaMask and refresh the page.");
    throw new Error("MetaMask not detected.");
  }

  return ethereum.request({
    method: "personal_sign",
    params: [message, address],
  });
}

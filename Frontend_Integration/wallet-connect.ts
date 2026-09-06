declare global {
  interface Window {
    ethereum?: any;
  }
}

export async function connectWallet(): Promise<string | null> {
  if (typeof window === "undefined") {
    throw new Error("Browser environment not available.");
  }

  if (!window.ethereum) {
    alert("Please install or enable MetaMask.");
    return null;
  }

  const accounts = await window.ethereum.request({
    method: "eth_requestAccounts",
  });

  if (!accounts || accounts.length === 0) {
    alert("No accounts found. Please unlock MetaMask.");
    return null;
  }

  return accounts[0];
}

export async function getConnectedAddress(): Promise<string | null> {
  if (typeof window === "undefined") return null;

  if (!window.ethereum) return null;

  const accounts: string[] = await window.ethereum.request({ method: "eth_accounts" });
  return accounts[0] ?? null;
}

export async function signMessage(address: string, message: string): Promise<string> {
  if (typeof window === "undefined") {
    throw new Error("Browser environment not available.");
  }

  if (!window.ethereum) {
    alert("Please install or enable MetaMask.");
    throw new Error("MetaMask not detected.");
  }

  return window.ethereum.request({
    method: "personal_sign",
    params: [message, address],
  });
}

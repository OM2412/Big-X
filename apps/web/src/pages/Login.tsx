import { useAuth } from "../hooks";

export default function Login() {
  const { login, loading } = useAuth();

  return (
    <div style={{ textAlign: "center", marginTop: "20vh" }}>
      <h1>Agentic DeFi Platform</h1>
      <p>Sign in with your wallet</p>
      <button onClick={login} disabled={loading}>
        {loading ? "Connecting..." : "Connect Wallet"}
      </button>
    </div>
  );
}

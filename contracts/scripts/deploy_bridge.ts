import fs from "node:fs";
import path from "node:path";
import { createPublicClient, createWalletClient, http } from "viem";
import { hardhat } from "viem/chains";
import { privateKeyToAccount } from "viem/accounts";

const RPC_URL = process.env.RPC_URL || "http://localhost:8545";

const privateKey = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80";
const account = privateKeyToAccount(privateKey);

const publicClient = createPublicClient({ chain: hardhat, transport: http(RPC_URL) });
const walletClient = createWalletClient({ chain: hardhat, transport: http(RPC_URL), account });

function readArtifact(contractName: string): any {
  const srcDir = path.join(process.cwd(), "artifacts", "src");
  const candidates = [
    path.join(srcDir, `${contractName}.sol`, `${contractName}.json`),
    path.join(srcDir, "test", `${contractName}.sol`, `${contractName}.json`),
  ];
  for (const file of candidates) {
    if (fs.existsSync(file)) {
      return JSON.parse(fs.readFileSync(file, "utf-8"));
    }
  }
  throw new Error(`Artifact not found for ${contractName}`);
}

async function main() {
  const mockWbtcArtifact = readArtifact("MockWbtc");
  const bridgeArtifact = readArtifact("BridgeContract");

  const wbtcHash = await walletClient.deployContract({
    abi: mockWbtcArtifact.abi,
    bytecode: mockWbtcArtifact.bytecode,
    args: [1_000_000_00000000n],
    account,
  });

  const wbtcReceipt = await publicClient.waitForTransactionReceipt({ hash: wbtcHash });
  const wbtcAddress = wbtcReceipt.contractAddress;
  console.log("MockWbtc deployed to:", wbtcAddress);

  const bridgeHash = await walletClient.deployContract({
    abi: bridgeArtifact.abi,
    bytecode: bridgeArtifact.bytecode,
    args: [wbtcAddress, account.address, [account.address], 1n],
    account,
  });

  const bridgeReceipt = await publicClient.waitForTransactionReceipt({ hash: bridgeHash });
  console.log("BridgeContract deployed to:", bridgeReceipt.contractAddress);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});

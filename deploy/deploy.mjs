#!/usr/bin/env node
/**
 * Deploy contracts/build/Primacy.deploy.py to GenLayer Studio Dev (chain
 * 61997) and write the resulting address into deploy/deployments.json.
 *
 * Run `python contracts/build_bundle.py` first if the contract source
 * changed -- this script deploys the bundled artifact, not the two-file
 * dev source (see docs/architecture.md).
 *
 * Required env (.env, never committed):
 *   DEPLOYER_PRIVATE_KEY  -- a real, funded Studio Dev account's private key
 *   TREASURY_ADDRESS      -- a real EOA (NOT an Intelligent Contract address --
 *                            see docs/architecture.md's "self-service payouts"
 *                            section for why a contract address here would
 *                            silently swallow half of every settlement fee)
 *
 * This is a real, funds-moving, on-chain action -- run it deliberately,
 * not as part of an automated pipeline without review.
 */
import { createAccount, createClient } from "genlayer-js";
import { studioDevnet } from "genlayer-js/chains";
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, "..");
const CONTRACT_PATH = join(REPO_ROOT, "contracts", "build", "Primacy.deploy.py");
const DEPLOYMENTS_PATH = join(__dirname, "deployments.json");

function isEoaAddress(addr) {
  return /^0x[0-9a-fA-F]{40}$/.test(addr);
}

async function main() {
  const privateKey = process.env.DEPLOYER_PRIVATE_KEY;
  const treasury = process.env.TREASURY_ADDRESS;

  if (!privateKey) throw new Error("DEPLOYER_PRIVATE_KEY is not set (see .env.example)");
  if (!treasury || !isEoaAddress(treasury)) {
    throw new Error("TREASURY_ADDRESS must be a plain 0x + 40 hex char EOA address (see .env.example)");
  }
  if (!existsSync(CONTRACT_PATH)) {
    throw new Error(`${CONTRACT_PATH} not found -- run "python contracts/build_bundle.py" first`);
  }

  const code = readFileSync(CONTRACT_PATH, "utf-8");
  const account = createAccount(privateKey);
  const client = createClient({ chain: studioDevnet, account });

  console.log(`Deploying Primacy to Studio Dev (chain ${studioDevnet.id})...`);
  console.log(`Treasury: ${treasury}`);

  // Consensus v0.6's fee system requires an explicit, non-zero `fees`
  // object on every write/deploy (FeeValueMustBeNonZero otherwise) --
  // see docs/architecture.md. Verified directly against the installed
  // genlayer-js@2.0.0-rc.1's own .d.ts (node_modules/genlayer-js/dist/
  // index-BT1ApAqQ.d.ts): estimateTransactionFees(args?: FeeEstimateOptions)
  // takes an OPTIONAL options object (every field optional), not the
  // {transaction:{...}} shape an earlier draft of this script guessed --
  // calling it with no args lets the SDK fill in network defaults.
  // deployContract's `fees` param is typed TransactionFeeOptions
  // ({distribution?, messageAllocations?, feeValue?}), a subset of what
  // estimateTransactionFees returns (TransactionFeeEstimate also has a
  // `policy` field) -- passing the estimate straight through is fine,
  // the extra field is structurally harmless.
  const fees = await client.estimateTransactionFees();

  const hash = await client.deployContract({
    code,
    args: [treasury],
    fees,
  });
  console.log(`Deploy tx: ${hash}`);

  // waitForTransactionReceipt's `status` param is deprecated in favor of
  // `waitUntil: "decided" | "finalized"` (confirmed in the same .d.ts).
  const receipt = await client.waitForTransactionReceipt({ hash, waitUntil: "decided" }).catch((err) => {
    console.warn(`waitForTransactionReceipt(decided) did not resolve cleanly (${err.message}); ` +
      "checking getTransaction() directly instead -- this can happen on a genuinely successful deploy.");
    return null;
  });

  const tx = receipt ?? (await client.getTransaction({ hash }));
  const address = tx?.txDataDecoded?.contractAddress;
  if (!address) {
    console.error("Could not read the deployed contract address from the transaction result.");
    console.error("Raw transaction:", JSON.stringify(tx, null, 2));
    process.exit(1);
  }
  console.log(`Deployed at: ${address}`);
  console.log(`Explorer: https://explorer-studio-dev.genlayer.com/address/${address}`);

  const existing = existsSync(DEPLOYMENTS_PATH)
    ? JSON.parse(readFileSync(DEPLOYMENTS_PATH, "utf-8"))
    : { deployments: [] };
  existing.deployments.push({
    address,
    txHash: hash,
    chainId: studioDevnet.id,
    treasury,
    deployedAt: new Date().toISOString(),
  });
  writeFileSync(DEPLOYMENTS_PATH, JSON.stringify(existing, null, 2) + "\n");
  console.log(`Wrote ${DEPLOYMENTS_PATH}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});

#!/usr/bin/env node
/**
 * Full PRIMACY lifecycle walkthrough -- create -> bet -> settle -> claim
 * -- against a LOCAL GenLayer Studio node (genlayer-js's `localnet`
 * chain preset). Written so the protocol can be proven end to end
 * without touching Studio Dev at all, since Studio Dev is currently
 * unable to load any contract over ~305 bytes (see docs/STATUS.md) --
 * this script deliberately never sends a transaction to studio-dev.
 *
 * Prerequisites (not automated by this script -- see GenLayer's own
 * docs for the current, correct way to stand up a local node; the
 * exact CLI incantation has changed across RC releases and is not
 * re-verified here):
 *   1. A local GenLayer Studio node running and reachable at
 *      LOCALNET_RPC_URL (default http://localhost:4000/api).
 *   2. `python contracts/build_bundle.py` already run (this script
 *      deploys contracts/build/Primacy.deploy.py, not the two-file dev
 *      source).
 *
 * Funding: localnet accounts are funded via the `sim_fundAccount`
 * JSON-RPC method with a real GEN amount in WEI (a bare small number
 * like `10` funds 10 WEI, not 10 GEN -- confirmed gotcha, see
 * genlayer-cli-tooling-gotchas memory). This script funds two fresh,
 * throwaway, generated-on-the-spot keys (creator/settler and bettor) --
 * nothing here touches a real wallet or spends real GEN on any live
 * network.
 *
 * Real-time constraint, not a shortcut: PRIMACY enforces
 * MIN_LEAD_SECONDS (30 min) and an hour-boundary start, and betting
 * closes exactly at that boundary. This script computes the soonest
 * legal hour, creates the market, places a bet, then genuinely polls
 * in real time until that hour has elapsed before settling -- there is
 * no fast-forward. Expect this script to take 30-90 minutes end to end
 * depending on how close "now" is to the next eligible hour boundary.
 * Run it, then walk away and check back.
 *
 * Usage:
 *   node deploy/local_walkthrough.mjs
 *
 * Env overrides:
 *   LOCALNET_RPC_URL   (default http://localhost:4000/api)
 *   LOCALNET_CHAIN_ID  (default 61999 -- confirm against your local node)
 */
import { createAccount, createClient, generatePrivateKey } from "genlayer-js";
import { localnet } from "genlayer-js/chains";
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, "..");
const CONTRACT_PATH = join(REPO_ROOT, "contracts", "build", "Primacy.deploy.py");

const RPC_URL = process.env.LOCALNET_RPC_URL ?? "http://localhost:4000/api";
const CHAIN = { ...localnet, rpcUrls: { default: { http: [RPC_URL] } } };

const GEN = 10n ** 18n;
const HOUR = 3600;
const MIN_LEAD = 1800;

function log(step, msg) {
  console.log(`[${new Date().toISOString()}] ${step}: ${msg}`);
}

async function fundAccount(client, address, genAmount) {
  const amountWei = genAmount * 10 ** 18; // sim_fundAccount takes a plain number, in WEI
  await client.request({ method: "sim_fundAccount", params: [address, amountWei] });
}

async function waitForTerminal(client, hash, label) {
  const tx = await client
    .waitForTransactionReceipt({ hash, waitUntil: "finalized", retries: 120, interval: 3000 })
    .catch(() => client.getTransaction({ hash }));
  if (tx?.txExecutionResultName !== "FINISHED_WITH_RETURN") {
    throw new Error(
      `${label} did not succeed: ${tx?.txExecutionResultName ?? tx?.statusName ?? "no receipt"}`,
    );
  }
  return tx;
}

async function main() {
  if (!existsSync(CONTRACT_PATH)) {
    throw new Error(`${CONTRACT_PATH} not found -- run "python contracts/build_bundle.py" first`);
  }
  const code = readFileSync(CONTRACT_PATH, "utf-8");

  const creatorKey = generatePrivateKey();
  const bettorKey = generatePrivateKey();
  const creator = createAccount(creatorKey);
  const bettor = createAccount(bettorKey);
  log("setup", `creator=${creator.address} bettor=${bettor.address}`);

  const fundingClient = createClient({ chain: CHAIN });
  await fundAccount(fundingClient, creator.address, 20);
  await fundAccount(fundingClient, bettor.address, 20);
  log("setup", "funded both accounts with 20 GEN each via sim_fundAccount");

  const creatorClient = createClient({ chain: CHAIN, account: creator });
  const bettorClient = createClient({ chain: CHAIN, account: bettor });

  // 1. Deploy
  const deployFees = await creatorClient.estimateTransactionFees();
  const treasury = creator.address; // fine for a throwaway local walkthrough
  const deployHash = await creatorClient.deployContract({
    code,
    args: [treasury],
    fees: deployFees,
  });
  const deployTx = await waitForTerminal(creatorClient, deployHash, "deploy");
  const contractAddress = deployTx.txDataDecoded?.contractAddress;
  if (!contractAddress) throw new Error("deploy succeeded but no contract address decoded");
  log("deploy", `contract=${contractAddress}`);

  // 2. Create market -- soonest legal hour boundary at least MIN_LEAD past now
  const now = Math.floor(Date.now() / 1000);
  const start = Math.ceil((now + MIN_LEAD + 60) / HOUR) * HOUR; // +60s safety margin
  log("create_market", `start=${start} (${new Date(start * 1000).toISOString()})`);
  const createFees = await creatorClient.estimateTransactionFees();
  const createHash = await creatorClient.writeContract({
    address: contractAddress,
    functionName: "create_market",
    kwargs: { lane_id: "MAJORS", start },
    value: 2n * GEN,
    fees: createFees,
  });
  await waitForTerminal(creatorClient, createHash, "create_market");
  const marketId = await creatorClient.readContract({
    address: contractAddress,
    functionName: "get_market_by_lane_start",
    kwargs: { lane_id: "MAJORS", start },
  });
  log("create_market", `market_id=${marketId}`);

  // 3. Place bet
  const betFees = await bettorClient.estimateTransactionFees();
  const betHash = await bettorClient.writeContract({
    address: contractAddress,
    functionName: "place_bet",
    kwargs: { market_id: marketId, symbol: "BTC" },
    value: 5n * GEN,
    fees: betFees,
  });
  await waitForTerminal(bettorClient, betHash, "place_bet");
  log("place_bet", "bettor staked 5 GEN on BTC");

  // 4. Wait for real time to pass -- no shortcut, see header comment
  const waitSeconds = start + HOUR - Math.floor(Date.now() / 1000) + 30;
  log("wait", `sleeping ${Math.ceil(waitSeconds / 60)} min until the hour completes`);
  await new Promise((r) => setTimeout(r, Math.max(0, waitSeconds) * 1000));

  // 5. Settle -- real gl.vm.run_nondet consensus against the real locked venues
  const settleFees = await creatorClient.estimateTransactionFees();
  const settleHash = await creatorClient.writeContract({
    address: contractAddress,
    functionName: "settle_market",
    kwargs: { market_id: marketId },
    value: 1n * GEN,
    fees: settleFees,
  });
  await waitForTerminal(creatorClient, settleHash, "settle_market");
  const market = await creatorClient.readContract({
    address: contractAddress,
    functionName: "get_market",
    kwargs: { market_id: marketId },
  });
  log("settle_market", `state=${market.state} winner=${market.winner}`);

  // 6. Claim (winner) or claim_refund (inconclusive)
  if (market.state === "SETTLED" && market.winner === "BTC") {
    const claimFees = await bettorClient.estimateTransactionFees();
    const claimHash = await bettorClient.writeContract({
      address: contractAddress,
      functionName: "claim",
      kwargs: { market_id: marketId },
      value: 0n,
      fees: claimFees,
    });
    const tx = await waitForTerminal(bettorClient, claimHash, "claim");
    log("claim", `payout tx=${tx.hash}`);
  } else {
    const refundFees = await bettorClient.estimateTransactionFees();
    const refundHash = await bettorClient.writeContract({
      address: contractAddress,
      functionName: "claim_refund",
      kwargs: { market_id: marketId },
      value: 0n,
      fees: refundFees,
    });
    const tx = await waitForTerminal(bettorClient, refundHash, "claim_refund");
    log("claim_refund", `refund tx=${tx.hash} (market was ${market.state}, or BTC did not win)`);
  }

  log("done", `full lifecycle complete at contract ${contractAddress}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});

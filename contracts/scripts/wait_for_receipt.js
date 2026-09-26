/**
 * Poll a receipt through raw JSON-RPC.
 *
 * Some public Ethereum RPC responses have caused ethers' transaction parser
 * to reject an otherwise-confirmed transaction (for example, `to: ""` on a
 * contract-creation response). Receipt polling only needs the transaction
 * hash and avoids parsing that malformed transaction object.
 */
async function waitForReceipt(rpcUrl, txHash, timeoutMs = 120_000) {
  const startedAt = Date.now();

  while (Date.now() - startedAt < timeoutMs) {
    const response = await fetch(rpcUrl, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        jsonrpc: "2.0",
        id: 1,
        method: "eth_getTransactionReceipt",
        params: [txHash],
      }),
    });

    if (!response.ok) {
      throw new Error(`RPC receipt request failed: HTTP ${response.status}`);
    }

    const payload = await response.json();
    if (payload.error) {
      throw new Error(`RPC receipt request failed: ${payload.error.message || JSON.stringify(payload.error)}`);
    }
    if (payload.result) {
      return payload.result;
    }

    await new Promise((resolve) => setTimeout(resolve, 1_500));
  }

  throw new Error(`Timed out waiting for transaction receipt: ${txHash}`);
}

module.exports = { waitForReceipt };

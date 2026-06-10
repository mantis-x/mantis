const { expect }        = require("chai");
const { ethers }        = require("hardhat");
const { loadFixture }   = require("@nomicfoundation/hardhat-toolbox/network-helpers");

// ─── Helpers ────────────────────────────────────────────────────────────────

/**
 * Build the canonical signal JSON exactly as the Python delivery worker does.
 * Key order must be alphabetical and stable.
 */
function canonicalJSON(overrides = {}) {
  const defaults = {
    confidence:  82,
    deliver_at:  "2026-07-01T10:30:00Z",
    id:          0,
    pool:        "0xAbCd1234AbCd1234AbCd1234AbCd1234AbCd1234",
    protocol:    "agni_finance",
    signal_type: "accumulation",
    summary:     "Smart money accumulated 2.1M USDY across three Agni pools.",
  };
  return JSON.stringify({ ...defaults, ...overrides });
}

function hashPayload(json) {
  return ethers.keccak256(ethers.toUtf8Bytes(json));
}

// ─── Fixture ────────────────────────────────────────────────────────────────

async function deployFixture() {
  const [owner, logger, stranger, other] = await ethers.getSigners();
  const Factory  = await ethers.getContractFactory("SignalAuditLog");
  const contract = await Factory.deploy(logger.address);
  return { contract, owner, logger, stranger, other };
}

// ─── Tests ──────────────────────────────────────────────────────────────────

describe("SignalAuditLog", function () {

  // ── Deployment ────────────────────────────────────────────────────────────

  describe("deployment", function () {
    it("sets owner to deployer", async function () {
      const { contract, owner } = await loadFixture(deployFixture);
      expect(await contract.owner()).to.equal(owner.address);
    });

    it("sets authorisedLogger to provided address", async function () {
      const { contract, logger } = await loadFixture(deployFixture);
      expect(await contract.authorisedLogger()).to.equal(logger.address);
    });

    it("falls back to deployer when logger arg is zero address", async function () {
      const [owner] = await ethers.getSigners();
      const Factory  = await ethers.getContractFactory("SignalAuditLog");
      const contract = await Factory.deploy(ethers.ZeroAddress);
      expect(await contract.authorisedLogger()).to.equal(owner.address);
    });

    it("starts with totalSignals = 0", async function () {
      const { contract } = await loadFixture(deployFixture);
      expect(await contract.totalSignals()).to.equal(0n);
    });
  });

  // ── logSignal ────────────────────────────────────────────────────────────

  describe("logSignal", function () {
    it("returns signalId 0 for the first signal", async function () {
      const { contract, logger } = await loadFixture(deployFixture);
      const json    = canonicalJSON();
      const hash    = hashPayload(json);

      const tx = await contract.connect(logger).logSignal(hash, "agni_finance", "accumulation", 82);
      const rc = await tx.wait();
      const id = rc.logs[0].args[0];
      expect(id).to.equal(0n);
    });

    it("increments totalSignals after each log", async function () {
      const { contract, logger } = await loadFixture(deployFixture);

      await contract.connect(logger).logSignal(hashPayload(canonicalJSON({ id: 0 })), "agni_finance",   "accumulation", 82);
      await contract.connect(logger).logSignal(hashPayload(canonicalJSON({ id: 1 })), "merchant_moe",   "whale_entry",  75);
      await contract.connect(logger).logSignal(hashPayload(canonicalJSON({ id: 2 })), "fluxion",        "distribution", 68);

      expect(await contract.totalSignals()).to.equal(3n);
    });

    it("emits SignalLogged with correct fields", async function () {
      const { contract, logger } = await loadFixture(deployFixture);
      const json = canonicalJSON();
      const hash = hashPayload(json);

      await expect(
        contract.connect(logger).logSignal(hash, "agni_finance", "accumulation", 82)
      )
        .to.emit(contract, "SignalLogged")
        .withArgs(0n, hash, "agni_finance", "accumulation", 82, await ethers.provider.getBlock("latest").then(b => b?.timestamp + 1));
    });

    it("increments signalsByProtocol correctly", async function () {
      const { contract, logger } = await loadFixture(deployFixture);

      await contract.connect(logger).logSignal(hashPayload(canonicalJSON({ id: 0 })), "agni_finance", "accumulation", 80);
      await contract.connect(logger).logSignal(hashPayload(canonicalJSON({ id: 1 })), "agni_finance", "whale_entry",  70);
      await contract.connect(logger).logSignal(hashPayload(canonicalJSON({ id: 2 })), "merchant_moe","distribution", 65);

      expect(await contract.signalsByProtocol("agni_finance")).to.equal(2n);
      expect(await contract.signalsByProtocol("merchant_moe")).to.equal(1n);
    });

    it("increments signalsByType correctly", async function () {
      const { contract, logger } = await loadFixture(deployFixture);

      await contract.connect(logger).logSignal(hashPayload(canonicalJSON({ id: 0 })), "agni_finance", "accumulation", 80);
      await contract.connect(logger).logSignal(hashPayload(canonicalJSON({ id: 1 })), "merchant_moe","accumulation", 75);

      expect(await contract.signalsByType("accumulation")).to.equal(2n);
    });

    it("stores logger address in the entry", async function () {
      const { contract, logger } = await loadFixture(deployFixture);
      await contract.connect(logger).logSignal(hashPayload(canonicalJSON()), "agni_finance", "accumulation", 82);
      const [,, storedLogger] = await contract.getEntry(0);
      expect(storedLogger).to.equal(logger.address);
    });

    // ── Access control ──────────────────────────────────────────────────────

    it("reverts when called by a non-logger", async function () {
      const { contract, stranger } = await loadFixture(deployFixture);
      await expect(
        contract.connect(stranger).logSignal(hashPayload(canonicalJSON()), "agni_finance", "accumulation", 82)
      ).to.be.revertedWithCustomError(contract, "OnlyLogger");
    });

    it("reverts on zero hash", async function () {
      const { contract, logger } = await loadFixture(deployFixture);
      await expect(
        contract.connect(logger).logSignal(ethers.ZeroHash, "agni_finance", "accumulation", 82)
      ).to.be.revertedWithCustomError(contract, "EmptyHash");
    });

    it("reverts when confidence > 100", async function () {
      const { contract, logger } = await loadFixture(deployFixture);
      await expect(
        contract.connect(logger).logSignal(hashPayload(canonicalJSON()), "agni_finance", "accumulation", 101)
      ).to.be.revertedWithCustomError(contract, "InvalidConfidence");
    });

    it("accepts confidence = 0 (edge case)", async function () {
      const { contract, logger } = await loadFixture(deployFixture);
      await expect(
        contract.connect(logger).logSignal(hashPayload(canonicalJSON()), "agni_finance", "accumulation", 0)
      ).to.not.be.reverted;
    });

    it("accepts confidence = 100 (edge case)", async function () {
      const { contract, logger } = await loadFixture(deployFixture);
      await expect(
        contract.connect(logger).logSignal(hashPayload(canonicalJSON()), "agni_finance", "accumulation", 100)
      ).to.not.be.reverted;
    });
  });

  // ── verify ───────────────────────────────────────────────────────────────

  describe("verify", function () {
    async function withOneSignal() {
      const { contract, logger, ...rest } = await loadFixture(deployFixture);
      const json = canonicalJSON({ id: 0, protocol: "agni_finance" });
      const hash = hashPayload(json);
      await contract.connect(logger).logSignal(hash, "agni_finance", "accumulation", 82);
      return { contract, logger, json, hash, ...rest };
    }

    it("returns valid=true for the correct payload", async function () {
      const { contract, json } = await withOneSignal();
      const [valid] = await contract.verify(0, json);
      expect(valid).to.be.true;
    });

    it("returns valid=false for a tampered payload", async function () {
      const { contract } = await withOneSignal();
      const tampered = canonicalJSON({ id: 0, confidence: 99 });   // changed field
      const [valid]  = await contract.verify(0, tampered);
      expect(valid).to.be.false;
    });

    it("returns valid=false for an empty string", async function () {
      const { contract } = await withOneSignal();
      const [valid] = await contract.verify(0, "");
      expect(valid).to.be.false;
    });

    it("returns the correct storedAt timestamp", async function () {
      const { contract } = await withOneSignal();
      const block = await ethers.provider.getBlock("latest");
      const [, storedAt] = await contract.verify(0, canonicalJSON({ id: 0 }));
      expect(storedAt).to.be.closeTo(BigInt(block.timestamp), 2n);
    });

    it("reverts for a signalId that does not exist", async function () {
      const { contract } = await withOneSignal();
      await expect(contract.verify(999, "{}"))
        .to.be.revertedWithCustomError(contract, "SignalNotFound")
        .withArgs(999);
    });
  });

  // ── verifyHash ───────────────────────────────────────────────────────────

  describe("verifyHash", function () {
    it("returns valid=true for the correct hash", async function () {
      const { contract, logger } = await loadFixture(deployFixture);
      const json = canonicalJSON();
      const hash = hashPayload(json);
      await contract.connect(logger).logSignal(hash, "agni_finance", "accumulation", 82);
      const [valid] = await contract.verifyHash(0, hash);
      expect(valid).to.be.true;
    });

    it("returns valid=false for the wrong hash", async function () {
      const { contract, logger } = await loadFixture(deployFixture);
      const hash = hashPayload(canonicalJSON({ id: 0 }));
      await contract.connect(logger).logSignal(hash, "agni_finance", "accumulation", 82);
      const wrongHash = hashPayload(canonicalJSON({ id: 99 }));
      const [valid]   = await contract.verifyHash(0, wrongHash);
      expect(valid).to.be.false;
    });
  });

  // ── getEntry ─────────────────────────────────────────────────────────────

  describe("getEntry", function () {
    it("returns all fields correctly", async function () {
      const { contract, logger } = await loadFixture(deployFixture);
      const json = canonicalJSON({ id: 0, protocol: "merchant_moe", signal_type: "whale_entry" });
      const hash = hashPayload(json);
      await contract.connect(logger).logSignal(hash, "merchant_moe", "whale_entry", 77);

      const [storedHash,, storedLogger, confidence, protocol, signalType] =
        await contract.getEntry(0);

      expect(storedHash).to.equal(hash);
      expect(storedLogger).to.equal(logger.address);
      expect(confidence).to.equal(77);
      expect(protocol).to.equal("merchant_moe");
      expect(signalType).to.equal("whale_entry");
    });

    it("reverts for a non-existent signalId", async function () {
      const { contract } = await loadFixture(deployFixture);
      await expect(contract.getEntry(0))
        .to.be.revertedWithCustomError(contract, "SignalNotFound")
        .withArgs(0);
    });
  });

  // ── getRecentIds ─────────────────────────────────────────────────────────

  describe("getRecentIds", function () {
    async function withFiveSignals() {
      const { contract, logger, ...rest } = await loadFixture(deployFixture);
      for (let i = 0; i < 5; i++) {
        await contract.connect(logger).logSignal(
          hashPayload(canonicalJSON({ id: i })), "agni_finance", "accumulation", 70 + i
        );
      }
      return { contract, logger, ...rest };
    }

    it("returns [4,3,2,1,0] for offset=0 limit=5", async function () {
      const { contract } = await withFiveSignals();
      const ids = await contract.getRecentIds(0, 5);
      expect(ids.map(Number)).to.deep.equal([4, 3, 2, 1, 0]);
    });

    it("returns [4,3] for offset=0 limit=2", async function () {
      const { contract } = await withFiveSignals();
      const ids = await contract.getRecentIds(0, 2);
      expect(ids.map(Number)).to.deep.equal([4, 3]);
    });

    it("returns [2,1,0] for offset=2 limit=3", async function () {
      const { contract } = await withFiveSignals();
      const ids = await contract.getRecentIds(2, 3);
      expect(ids.map(Number)).to.deep.equal([2, 1, 0]);
    });

    it("caps limit at 100", async function () {
      const { contract, logger } = await loadFixture(deployFixture);
      // Log 105 signals
      for (let i = 0; i < 105; i++) {
        await contract.connect(logger).logSignal(
          hashPayload(canonicalJSON({ id: i })), "agni_finance", "accumulation", 70
        );
      }
      const ids = await contract.getRecentIds(0, 200);
      expect(ids.length).to.equal(100);
    });

    it("returns empty array when totalSignals = 0", async function () {
      const { contract } = await loadFixture(deployFixture);
      const ids = await contract.getRecentIds(0, 10);
      expect(ids.length).to.equal(0);
    });

    it("returns empty array when offset exceeds total", async function () {
      const { contract } = await withFiveSignals();
      const ids = await contract.getRecentIds(99, 5);
      expect(ids.length).to.equal(0);
    });
  });

  // ── rotateLogger ─────────────────────────────────────────────────────────

  describe("rotateLogger", function () {
    it("updates authorisedLogger", async function () {
      const { contract, owner, other } = await loadFixture(deployFixture);
      await contract.connect(owner).rotateLogger(other.address);
      expect(await contract.authorisedLogger()).to.equal(other.address);
    });

    it("emits LoggerRotated event", async function () {
      const { contract, owner, logger, other } = await loadFixture(deployFixture);
      await expect(contract.connect(owner).rotateLogger(other.address))
        .to.emit(contract, "LoggerRotated")
        .withArgs(logger.address, other.address);
    });

    it("allows new logger to log after rotation", async function () {
      const { contract, owner, other } = await loadFixture(deployFixture);
      await contract.connect(owner).rotateLogger(other.address);
      await expect(
        contract.connect(other).logSignal(hashPayload(canonicalJSON()), "agni_finance", "accumulation", 80)
      ).to.not.be.reverted;
    });

    it("reverts if old logger tries to log after rotation", async function () {
      const { contract, owner, logger, other } = await loadFixture(deployFixture);
      await contract.connect(owner).rotateLogger(other.address);
      await expect(
        contract.connect(logger).logSignal(hashPayload(canonicalJSON()), "agni_finance", "accumulation", 80)
      ).to.be.revertedWithCustomError(contract, "OnlyLogger");
    });

    it("reverts when called by non-owner", async function () {
      const { contract, stranger, other } = await loadFixture(deployFixture);
      await expect(contract.connect(stranger).rotateLogger(other.address))
        .to.be.revertedWithCustomError(contract, "OnlyOwner");
    });

    it("reverts when new logger is zero address", async function () {
      const { contract, owner } = await loadFixture(deployFixture);
      await expect(contract.connect(owner).rotateLogger(ethers.ZeroAddress))
        .to.be.revertedWithCustomError(contract, "ZeroAddress");
    });
  });

  // ── gas snapshot ─────────────────────────────────────────────────────────

  describe("gas", function () {
    it("logSignal gas is within budget (<80,000)", async function () {
      const { contract, logger } = await loadFixture(deployFixture);
      const hash = hashPayload(canonicalJSON());
      const tx   = await contract.connect(logger).logSignal(hash, "agni_finance", "accumulation", 82);
      const rc   = await tx.wait();
      const gas  = Number(rc.gasUsed);
      console.log(`    logSignal gas: ${gas.toLocaleString()}`);
      expect(gas).to.be.lessThan(80_000);
    });

    it("verify gas is within budget (<30,000)", async function () {
      const { contract, logger } = await loadFixture(deployFixture);
      const json = canonicalJSON();
      const hash = hashPayload(json);
      await contract.connect(logger).logSignal(hash, "agni_finance", "accumulation", 82);
      const gas = await contract.verify.estimateGas(0, json);
      console.log(`    verify gas:    ${Number(gas).toLocaleString()}`);
      expect(gas).to.be.lessThan(30_000n);
    });
  });
});

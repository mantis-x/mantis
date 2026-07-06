"""Tests for ArbitrumSwapExecutor and executor chain routing."""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from src.arbitrum.swap_executor import (
    ArbitrumSwapExecutor,
    FEE_LOW,
    _MAINNET_WETH,
    _MAINNET_USDC,
)
from src.models.execution_request import ExecutionRequest, ActionType


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_request(chain="mantle", amount_usd=100.0, **kwargs):
    defaults = dict(
        agent_id     = 1,
        owner_wallet = "0xabc",
        signal_id    = "sig1",
        signal_type  = "accumulation",
        protocol     = "uniswap_v3",
        pool_address = "0xdeadbeef",
        confidence   = 85,
        z_score      = 3.5,
        action_type  = ActionType.SWAP,
        amount_usd   = amount_usd,
        max_slippage = 0.02,
        max_position = 5.0,
        chain        = chain,
    )
    defaults.update(kwargs)
    return ExecutionRequest(**defaults)


# ── ArbitrumSwapExecutor unit tests ──────────────────────────────────────────

class TestArbitrumSwapExecutorStubMode:
    """Tests when DEPLOYER_PRIVATE_KEY is absent → stub mode."""

    def setup_method(self):
        # Ensure no private key leaks in from the environment
        os.environ.pop("DEPLOYER_PRIVATE_KEY", None)
        self.executor = ArbitrumSwapExecutor(dry_run=True)

    def test_not_ready_without_key(self):
        assert not self.executor.is_ready()

    def test_stub_result_returned(self):
        result = self.executor.swap(_MAINNET_USDC, _MAINNET_WETH, 50.0)
        assert result["simulated"] is True
        assert result["tx_hash"].startswith("stub_arb_")
        assert result["amount_usd"] == 50.0

    def test_stub_result_has_required_keys(self):
        result = self.executor.swap(_MAINNET_USDC, _MAINNET_WETH, 10.0)
        for key in ("tx_hash", "amount_in", "amount_out", "gas_used", "simulated", "amount_usd"):
            assert key in result, f"Missing key: {key}"


class TestArbitrumSwapExecutorUsdToUnits:
    """Tests for the USD → token-units conversion."""

    def setup_method(self):
        os.environ.pop("DEPLOYER_PRIVATE_KEY", None)
        self.executor = ArbitrumSwapExecutor(dry_run=True)
        # Manually set _default_weth so _usd_to_units can compare
        self.executor._default_weth = _MAINNET_WETH

    def test_stablecoin_6_decimals(self):
        # USDC has 6 decimals; $50 → 50_000_000 units
        units = self.executor._usd_to_units(50.0, 6, _MAINNET_USDC)
        assert units == 50_000_000

    def test_stablecoin_18_decimals(self):
        units = self.executor._usd_to_units(1.0, 18, _MAINNET_USDC)
        assert units == 10 ** 18

    def test_weth_price_conversion(self):
        os.environ["ETH_PRICE_USD"] = "2400"
        # $2400 of WETH → 1 ETH → 10^18 wei
        units = self.executor._usd_to_units(2400.0, 18, _MAINNET_WETH)
        assert units == 10 ** 18

    def test_weth_partial_amount(self):
        os.environ["ETH_PRICE_USD"] = "2400"
        # $240 = 0.1 ETH → 0.1 * 10^18
        units = self.executor._usd_to_units(240.0, 18, _MAINNET_WETH)
        assert units == int(0.1 * 10 ** 18)


class TestChainlinkEthPrice:
    """Tests for the live Chainlink ETH/USD price feed with fallback."""

    def _make_executor_with_feed(self, answer, decimals=8):
        executor = ArbitrumSwapExecutor.__new__(ArbitrumSwapExecutor)
        executor._eth_price_cache = None
        executor._eth_price_cache_ts = 0.0
        mock_feed = MagicMock()
        mock_feed.functions.decimals.return_value.call.return_value = decimals
        mock_feed.functions.latestRoundData.return_value.call.return_value = (
            1, answer, 0, 0, 1,
        )
        executor._feed = mock_feed
        return executor

    def test_reads_price_from_feed(self):
        executor = self._make_executor_with_feed(answer=3_200_00000000)  # $3200.00000000 @ 8 decimals
        price = executor._get_eth_price_usd()
        assert abs(price - 3200.0) < 0.01

    def test_caches_price_for_60_seconds(self):
        executor = self._make_executor_with_feed(answer=3_200_00000000)
        first  = executor._get_eth_price_usd()
        # Change the mock's return value — cached call should NOT pick it up
        executor._feed.functions.latestRoundData.return_value.call.return_value = (
            1, 9_999_00000000, 0, 0, 1,
        )
        second = executor._get_eth_price_usd()
        assert first == second == pytest.approx(3200.0, rel=0.01)

    def test_falls_back_to_env_var_on_feed_error(self):
        os.environ["ETH_PRICE_USD"] = "2500"
        executor = ArbitrumSwapExecutor.__new__(ArbitrumSwapExecutor)
        executor._eth_price_cache = None
        executor._eth_price_cache_ts = 0.0
        mock_feed = MagicMock()
        mock_feed.functions.decimals.return_value.call.side_effect = Exception("rpc down")
        executor._feed = mock_feed
        price = executor._get_eth_price_usd()
        assert price == 2500.0


class TestArbitrumSwapExecutorDryRunWithMockedW3:
    """Tests for _simulate() path when w3 is mocked."""

    def _make_ready_executor(self, quote_return=12345678):
        executor = ArbitrumSwapExecutor.__new__(ArbitrumSwapExecutor)
        executor._dry_run     = True
        executor._ready       = True
        executor._router_addr = _MAINNET_WETH  # dummy
        executor._default_weth= _MAINNET_WETH
        executor._default_usdc= _MAINNET_USDC

        # Mock router contract
        mock_router = MagicMock()
        mock_router.functions.exactInputSingle.return_value.call.return_value = quote_return
        executor._router = mock_router

        # Mock account
        mock_account = MagicMock()
        mock_account.address = "0x1234"
        executor._account = mock_account

        # Mock w3
        mock_w3 = MagicMock()
        mock_token_contract = MagicMock()
        mock_token_contract.functions.decimals.return_value.call.return_value = 6
        mock_w3.eth.contract.return_value = mock_token_contract
        from web3 import Web3
        mock_w3.to_checksum_address = Web3.to_checksum_address
        executor._w3 = mock_w3

        return executor

    def test_dry_run_returns_simulated_true(self):
        executor = self._make_ready_executor(quote_return=5_000_000)
        result = executor.swap(_MAINNET_USDC, _MAINNET_WETH, 5.0)
        assert result["simulated"] is True
        assert result["tx_hash"].startswith("dry_run_arb_")

    def test_dry_run_returns_quoted_amount_out(self):
        executor = self._make_ready_executor(quote_return=987654)
        result = executor.swap(_MAINNET_USDC, _MAINNET_WETH, 5.0)
        assert result["amount_out"] == 987654

    def test_dry_run_amount_usd_preserved(self):
        executor = self._make_ready_executor()
        result = executor.swap(_MAINNET_USDC, _MAINNET_WETH, 42.0)
        assert result["amount_usd"] == 42.0

    def test_dry_run_tolerates_simulation_failure(self):
        """If the on-chain call() reverts (e.g. no liquidity on testnet), amount_out=0."""
        executor = self._make_ready_executor()
        executor._router.functions.exactInputSingle.return_value.call.side_effect = Exception("revert")
        result = executor.swap(_MAINNET_USDC, _MAINNET_WETH, 5.0)
        assert result["simulated"] is True
        assert result["amount_out"] == 0


class TestArbitrumSwapExecutorLiveModeSlippage:
    """
    Live trades (dry_run=False) must derive amountOutMinimum from an on-chain
    quote, not accept-any-output — this is the real slippage protection.
    """

    def _make_live_executor(self, quote_out=1_000_000):
        executor = ArbitrumSwapExecutor.__new__(ArbitrumSwapExecutor)
        executor._dry_run      = False
        executor._ready        = True
        executor._router_addr  = _MAINNET_WETH  # dummy
        executor._default_weth = _MAINNET_WETH
        executor._default_usdc = _MAINNET_USDC

        mock_quoter = MagicMock()
        mock_quoter.functions.quoteExactInputSingle.return_value.call.return_value = (
            quote_out, 0, 0, 0,
        )
        executor._quoter = mock_quoter

        mock_router = MagicMock()
        executor._router = mock_router

        mock_account = MagicMock()
        mock_account.address = "0x1234"
        executor._account = mock_account

        mock_w3 = MagicMock()
        mock_token_contract = MagicMock()
        mock_token_contract.functions.decimals.return_value.call.return_value = 6
        mock_token_contract.functions.allowance.return_value.call.return_value = 10 ** 30
        mock_w3.eth.contract.return_value = mock_token_contract
        mock_w3.eth.get_transaction_count.return_value = 0
        mock_w3.eth.gas_price = 100_000_000
        mock_w3.eth.send_raw_transaction.return_value = b"\x01" * 32
        mock_w3.eth.wait_for_transaction_receipt.return_value = {"status": 1, "gasUsed": 100_000}
        mock_w3.eth.account.sign_transaction.return_value = MagicMock(rawTransaction=b"\x02" * 10)
        from web3 import Web3
        mock_w3.to_checksum_address = Web3.to_checksum_address
        executor._w3 = mock_w3
        executor._chain_id = 42161

        return executor

    def test_live_swap_queries_quoter(self):
        executor = self._make_live_executor(quote_out=1_000_000)
        executor.swap(_MAINNET_USDC, _MAINNET_WETH, 5.0, slippage=0.02)
        executor._quoter.functions.quoteExactInputSingle.assert_called_once()

    def test_live_swap_uses_quote_derived_min_out(self):
        """amountOutMinimum passed to exactInputSingle == quote * (1 - slippage)."""
        executor = self._make_live_executor(quote_out=1_000_000)
        executor.swap(_MAINNET_USDC, _MAINNET_WETH, 5.0, slippage=0.02)
        call_args = executor._router.functions.exactInputSingle.call_args
        params = call_args[0][0]
        amount_out_min = params[5]
        assert amount_out_min == int(1_000_000 * 0.98)

    def test_live_swap_aborts_when_quoter_fails(self):
        """A live trade must never fall back to accept-any-output on quoter failure."""
        executor = self._make_live_executor()
        executor._quoter.functions.quoteExactInputSingle.return_value.call.side_effect = (
            Exception("quoter reverted")
        )
        with pytest.raises(RuntimeError, match="Quoter call failed"):
            executor.swap(_MAINNET_USDC, _MAINNET_WETH, 5.0)
        executor._router.functions.exactInputSingle.assert_not_called()


# ── Executor chain routing tests ──────────────────────────────────────────────

class TestExecutorChainRouting:
    """Verify Executor._do_swap() routes by request.chain."""

    def _make_executor(self):
        """Build a minimal Executor with all sub-components mocked."""
        from src.executor import Executor
        with patch("src.executor.AgentRegistry"), \
             patch("src.executor.ByrealCLIRunner"), \
             patch("src.executor.ERC8004Logger"), \
             patch("src.executor.ArbitrumSwapExecutor"):
            ex = Executor.__new__(Executor)
            ex.byreal       = MagicMock()
            ex.arb_executor = MagicMock()
            ex.arb_executor.is_ready.return_value = True
            ex.guards       = MagicMock()
            ex.identity     = MagicMock()
            ex.registry     = MagicMock()
            ex.rule_eng     = MagicMock()
            return ex

    def test_arbitrum_chain_calls_arb_executor(self):
        ex = self._make_executor()
        request = make_request(chain="arbitrum")
        ex.arb_executor.swap.return_value = {
            "tx_hash": "dry_run_arb_123", "simulated": True,
            "amount_in": 0, "amount_out": 0, "gas_used": 0, "amount_usd": 100.0,
        }
        result = ex._do_swap(request)
        ex.arb_executor.swap.assert_called_once()
        ex.byreal.swap_execute.assert_not_called()

    def test_mantle_chain_calls_byreal(self):
        ex = self._make_executor()
        request = make_request(chain="mantle")
        ex.byreal.swap_execute.return_value = {"tx_hash": "mantle_tx_abc"}
        result = ex._do_swap(request)
        ex.byreal.swap_execute.assert_called_once()
        ex.arb_executor.swap.assert_not_called()

    def test_default_chain_calls_byreal(self):
        """No chain field → defaults to mantle → Byreal."""
        ex = self._make_executor()
        request = make_request(chain="mantle")
        ex.byreal.swap_execute.return_value = {"tx_hash": "mantle_tx_def"}
        ex._do_swap(request)
        ex.byreal.swap_execute.assert_called_once()

    def test_arbitrum_uses_usdc_as_default_token_in(self):
        ex = self._make_executor()
        request = make_request(chain="arbitrum", input_token=None)
        ex.arb_executor.swap.return_value = {
            "tx_hash": "dry_run_arb_456", "simulated": True,
            "amount_in": 0, "amount_out": 0, "gas_used": 0, "amount_usd": 50.0,
        }
        ex._do_swap(request)
        call_kwargs = ex.arb_executor.swap.call_args
        token_in = call_kwargs[1].get("token_in") or call_kwargs[0][0]
        assert token_in == "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8"  # USDC.e

    def test_arbitrum_uses_weth_as_default_token_out(self):
        ex = self._make_executor()
        request = make_request(chain="arbitrum", output_token=None)
        ex.arb_executor.swap.return_value = {
            "tx_hash": "dry_run_arb_789", "simulated": True,
            "amount_in": 0, "amount_out": 0, "gas_used": 0, "amount_usd": 50.0,
        }
        ex._do_swap(request)
        call_kwargs = ex.arb_executor.swap.call_args
        token_out = call_kwargs[1].get("token_out") or call_kwargs[0][1]
        assert token_out == "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"  # WETH

    def test_arbitrum_passes_slippage_from_request(self):
        ex = self._make_executor()
        request = make_request(chain="arbitrum", max_slippage=0.005)
        ex.arb_executor.swap.return_value = {
            "tx_hash": "dry_run_arb_slippage", "simulated": True,
            "amount_in": 0, "amount_out": 0, "gas_used": 0, "amount_usd": 100.0,
        }
        ex._do_swap(request)
        call_kwargs = ex.arb_executor.swap.call_args
        slippage = call_kwargs[1].get("slippage") or call_kwargs[0][3]
        assert slippage == 0.005


# ── Guard pipeline with Arbitrum requests ─────────────────────────────────────

class TestGuardPipelineArbitrum:
    """Guards must run identically for Arbitrum and Mantle requests."""

    def test_position_cap_applies_to_arbitrum(self):
        from src.guards.guard_runner import GuardRunner
        runner  = GuardRunner()
        request = make_request(chain="arbitrum", amount_usd=99999.0)
        result  = runner.check(request)
        assert not result  # should be blocked by position cap

    def test_normal_arbitrum_request_passes_guards(self):
        from src.guards.guard_runner import GuardRunner
        runner  = GuardRunner()
        request = make_request(chain="arbitrum", amount_usd=100.0)
        result  = runner.check(request)
        assert result  # should pass

    def test_slippage_guard_applies_to_arbitrum(self):
        from src.guards.guard_runner import GuardRunner
        runner  = GuardRunner()
        request = make_request(chain="arbitrum", max_slippage=0.99)
        result  = runner.check(request)
        assert not result  # slippage too high

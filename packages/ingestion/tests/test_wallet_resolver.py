"""Tests for WalletResolver (fix b) — resolving tx.origin behind proxy contracts."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.decoders.wallet_resolver import WalletResolver, _BUILTIN_PROXIES

NFPM = "0xc36442b4a4522e871399cd717abdd847ab11fe88"  # Uniswap V3 position manager
USER = "0xAbCdef0000000000000000000000000000001234"


class _Event:
    def __init__(self, wallet, tx="0xtxhash01"):
        self.wallet_address = wallet
        self.tx_hash = tx


class _FakeW3:
    def __init__(self, sender):
        self._sender = sender
        self.calls = 0
        self.eth = self

    def get_transaction(self, tx_hash):
        self.calls += 1
        return {"from": self._sender}


def test_proxy_address_resolved_to_origin():
    w3 = _FakeW3(USER)
    r = WalletResolver(w3, enabled=True)
    ev = r.resolve(_Event(NFPM))
    assert ev.wallet_address == USER.lower()   # lowercased EOA, not the NFPM
    assert w3.calls == 1


def test_non_proxy_address_untouched_and_no_rpc():
    w3 = _FakeW3(USER)
    r = WalletResolver(w3, enabled=True)
    ev = r.resolve(_Event("0xreal_eoa_wallet_addr_0000000000000000dead"))
    assert ev.wallet_address == "0xreal_eoa_wallet_addr_0000000000000000dead"
    assert w3.calls == 0   # a normal EOA never triggers a lookup


def test_tx_origin_cached_per_tx():
    w3 = _FakeW3(USER)
    r = WalletResolver(w3, enabled=True)
    r.resolve(_Event(NFPM, tx="0xsametx"))
    r.resolve(_Event(NFPM, tx="0xsametx"))
    assert w3.calls == 1   # second event on same tx served from cache


def test_disabled_is_noop():
    w3 = _FakeW3(USER)
    r = WalletResolver(w3, enabled=False)
    ev = r.resolve(_Event(NFPM))
    assert ev.wallet_address == NFPM
    assert w3.calls == 0


def test_rpc_failure_keeps_original():
    class _Boom(_FakeW3):
        def get_transaction(self, tx_hash):
            self.calls += 1
            raise RuntimeError("rpc down")

    w3 = _Boom(USER)
    r = WalletResolver(w3, enabled=True)
    ev = r.resolve(_Event(NFPM))
    assert ev.wallet_address == NFPM   # falls back to proxy addr, never raises


def test_nfpm_is_in_builtin_set():
    assert NFPM in _BUILTIN_PROXIES

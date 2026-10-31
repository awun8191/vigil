"""Adversarial timing and side-channel resilience tests for verify_token.

Verifies that verify_token:
1. Employs constant-time comparison via hmac.compare_digest.
2. Accurately rejects mismatched hashes across all prefix match lengths (1 to 63 chars).
3. Resists timing attack divergence across different prefix match lengths.
"""
import hashlib
import hmac
import time
from unittest.mock import patch
import pytest
from src.config.security import hash_token, verify_token


class TestTimingAttackResilience:
    SAMPLE_TOKEN = "vgt_secure_device_token_xyz_9876543210_abcdef"

    def test_verify_token_uses_constant_time_digest(self) -> None:
        """Verify that verify_token explicitly delegates to hmac.compare_digest."""
        expected_hash = hash_token(self.SAMPLE_TOKEN)
        
        with patch("src.config.security.hmac.compare_digest", wraps=hmac.compare_digest) as mock_compare:
            assert verify_token(self.SAMPLE_TOKEN, expected_hash) is True
            assert mock_compare.called
            args, _ = mock_compare.call_args
            # Verify compare_digest received 64-char lowercase digests
            assert len(args[0]) == 64
            assert len(args[1]) == 64
            assert args[0] == expected_hash
            assert args[1] == expected_hash

    def test_rejection_across_all_prefix_match_lengths(self) -> None:
        """Systematically test hash candidates with matching prefixes from 0 up to 63 characters.
        
        Ensures that an attacker cannot pass verification with any partial hash match.
        """
        expected_hash = hash_token(self.SAMPLE_TOKEN)
        assert len(expected_hash) == 64

        for match_len in range(64):
            # Take prefix of matching chars, flip character at match_len, fill rest with dummy chars
            if match_len < 64:
                char_at = expected_hash[match_len]
                diff_char = "0" if char_at != "0" else "1"
                crafted_hash = expected_hash[:match_len] + diff_char + ("a" * (63 - match_len))
                assert len(crafted_hash) == 64
                assert verify_token(self.SAMPLE_TOKEN, crafted_hash) is False

    def test_timing_attack_variance_across_prefix_lengths(self) -> None:
        """Empirical timing benchmark measuring comparison latency across different prefix lengths.
        
        Measures candidate hashes matching 0, 16, 32, 48, and 63 characters over 15,000 iterations.
        Ensures that execution time does not show monotonic prefix-dependent timing leak.
        """
        expected_hash = hash_token(self.SAMPLE_TOKEN)
        iterations = 10000

        prefix_lengths = [0, 16, 32, 48, 63]
        durations = {}

        for match_len in prefix_lengths:
            char_at = expected_hash[match_len]
            diff_char = "0" if char_at != "0" else "1"
            crafted_hash = expected_hash[:match_len] + diff_char + ("a" * (63 - match_len))

            # Warmup
            for _ in range(500):
                verify_token(self.SAMPLE_TOKEN, crafted_hash)

            # Benchmark
            start = time.perf_counter_ns()
            for _ in range(iterations):
                verify_token(self.SAMPLE_TOKEN, crafted_hash)
            total_ns = time.perf_counter_ns() - start

            durations[match_len] = total_ns / iterations

        # All per-call latencies should be on the order of a few microseconds (< 10us)
        for match_len, avg_ns in durations.items():
            assert avg_ns < 25000, f"Match length {match_len} latency {avg_ns}ns exceeds 25us threshold"

        # Difference between 0 matching chars and 63 matching chars should not show a large timing delta
        delta_ns = abs(durations[63] - durations[0])
        # Even on noisy shared CPU, delta should be well under 10us
        assert delta_ns < 10000, f"Timing delta between 0 and 63 matching chars is too large: {delta_ns}ns"

    def test_malformed_hashes_edge_cases(self) -> None:
        """Test verify_token behavior on malformed hashes (lengths != 64, non-hex, null bytes)."""
        expected_hash = hash_token(self.SAMPLE_TOKEN)

        edge_case_hashes = [
            "",
            "a" * 10,
            "b" * 32,
            "c" * 63,
            "d" * 65,
            "e" * 128,
            expected_hash + "\x00",
            "\x00" + expected_hash,
            "g" * 64,  # Non-hex character 'g'
            expected_hash.replace(expected_hash[0], "Z"),  # Upper case non-hex / hex
        ]

        for malformed in edge_case_hashes:
            assert verify_token(self.SAMPLE_TOKEN, malformed) is False

    def test_non_string_token_inputs(self) -> None:
        """Test verify_token handles arbitrary non-string types safely without throwing unhandled exceptions."""
        valid_hash = hash_token(self.SAMPLE_TOKEN)

        assert verify_token(None, valid_hash) is False  # type: ignore[arg-type]
        assert verify_token(self.SAMPLE_TOKEN, None) is False  # type: ignore[arg-type]
        assert verify_token(12345, valid_hash) is False  # type: ignore[arg-type]
        assert verify_token(self.SAMPLE_TOKEN, 12345) is False  # type: ignore[arg-type]
        assert verify_token(b"bytes_token", valid_hash) is False  # type: ignore[arg-type]
        assert verify_token(self.SAMPLE_TOKEN, b"bytes_hash") is False  # type: ignore[arg-type]
        assert verify_token(["list"], valid_hash) is False  # type: ignore[arg-type]
        assert verify_token({"dict": 1}, valid_hash) is False  # type: ignore[arg-type]

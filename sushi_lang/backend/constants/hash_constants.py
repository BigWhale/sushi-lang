"""Hash algorithm constants.

This module provides constants for the FNV-1a hash algorithm
used for consistent hashing across all types.
"""

# ============================================================================
# FNV-1a Hash Algorithm Constants
# ============================================================================
# 64-bit FNV-1a hash constants for consistent hashing across all types
# Algorithm: hash = (hash XOR byte) * FNV_PRIME

FNV1A_OFFSET_BASIS = 14695981039346656037  # 0xcbf29ce484222325 (64-bit)
FNV1A_PRIME = 1099511628211                # 0x100000001b3 (64-bit)

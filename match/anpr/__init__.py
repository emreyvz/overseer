"""Automatic number-plate recognition: pure normalization/voting + a lazy OCR backend.

Multi-country by design — there is no country format rule. Verifiability comes from
temporal consistency (the same normalized plate read across several frames) and OCR
confidence, not from a regex.
"""

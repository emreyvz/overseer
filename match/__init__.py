"""Reliable, verifiable live appearance matching.

A deterministic scoring core (`scoring`), typed contracts (`types`), pluggable model
encoders (`encoders`), ANPR (`anpr`) and an evaluation harness (`eval`). The heavy models
are backends behind stable interfaces; the pipeline, scoring, ANPR normalization and
metrics are pure Python and fully testable without any model download.
"""

"""AI-assisted video editing assistant — deterministic cleanup of talking-head footage.

Layers: transcribe → annotate → cut map → (review) → render → captions.
The cut map (cutmap.json) is the contract every step reads from.
"""

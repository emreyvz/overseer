"""Appearance encoders behind one interface. A deterministic baseline ships and always
works offline; specialized ReID / generic-embedding backends load lazily and report
``available() == False`` when their weights are missing so the engine can fall back."""

"""bmad-assist - CLI tool for automating BMAD methodology development loop."""

from importlib.metadata import version

try:
    __version__ = version("bmad-assist")
except Exception:
    __version__ = "0.0.0-dev"

"""This module and its data file must come from the uploaded ZIP."""
from pathlib import Path


def package_greeting(name: str) -> dict:
    """Return a greeting and the marker read from this package's supporting file."""
    return {"greeting": f"Hello, {name[:100]}!",
            "package_marker": Path(__file__).with_name("package-marker.txt").read_text().strip()}

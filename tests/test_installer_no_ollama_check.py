# tests/test_installer_no_ollama_check.py
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_installer_has_no_ollama_registry_check():
    text = (ROOT / "installer.iss").read_text(encoding="utf-8", errors="ignore")
    assert "Services\\Ollama" not in text
    assert "Ollama was not detected" not in text

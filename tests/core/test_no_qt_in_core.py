from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "core"


def test_core_never_imports_qt():
    offenders = []
    for p in CORE.rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        if "PySide6" in text or "PyQt" in text:
            offenders.append(p.name)
    assert offenders == []

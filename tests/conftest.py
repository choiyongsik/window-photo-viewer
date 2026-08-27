import os

# Must be set before any PySide6 import happens in the test session.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

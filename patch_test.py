import re

with open("tests/test_follow_edge_cases.py", "r") as f:
    content = f.read()

# Replace sleep loop
content = re.sub(
    r'for _ in range\(50\):\s+QCoreApplication.processEvents\(\)\s+time.sleep\(0.01\)',
    'thread = getattr(tab, "_inc_filter_thread", None)\n        if thread:\n            while thread.isRunning():\n                QCoreApplication.processEvents()',
    content
)

# Apply patch to test
with open("tests/test_follow_edge_cases.py", "w") as f:
    f.write(content)

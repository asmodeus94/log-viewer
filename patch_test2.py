import re

with open("tests/test_follow_edge_cases.py", "r") as f:
    content = f.read()

# Replace sleep loop
content = re.sub(
    r'# Aktywujemy Follow\n\s+tab\.cmd_toggle_follow\(\)',
    '# Aktywujemy Follow\n        with patch.object(tab.file_controller, "_follow_poll"):\n            tab.cmd_toggle_follow()',
    content
)

# Apply patch to test
with open("tests/test_follow_edge_cases.py", "w") as f:
    f.write(content)

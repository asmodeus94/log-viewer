import re

with open("tests/test_follow_edge_cases.py", "r") as f:
    content = f.read()

# Stop QTimers associated with the tab before processing events heavily or exiting
patch_str = """
        # Sprawdzamy czy timer przewijania podbil suwak (QTimer.singleShot w GUI loopie)
        # Nastepnie odpalamy 100 krokow by timer wygasl
        for _ in range(50):
            QCoreApplication.processEvents()
"""
replacement_str = """
        # Sprawdzamy czy timer przewijania podbil suwak (QTimer.singleShot w GUI loopie)
        # Nastepnie odpalamy 100 krokow by timer wygasl
        for _ in range(50):
            QCoreApplication.processEvents()

        # Ensure we turn off follow mode to stop any singleShot timers
        if tab.follow_active:
            with patch.object(tab.file_controller, "_follow_poll"):
                tab.cmd_toggle_follow()
"""
content = content.replace(patch_str, replacement_str)

with open("tests/test_follow_edge_cases.py", "w") as f:
    f.write(content)

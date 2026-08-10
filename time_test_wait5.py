import os
import sys

def test_pytest_process_events():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import pytest
    sys.exit(pytest.main(["tests/test_follow_edge_cases.py", "-v", "-s"]))

if __name__ == "__main__":
    test_pytest_process_events()

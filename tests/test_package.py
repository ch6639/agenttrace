"""包级冒烟测试：SDK 可导入、版本号正确。"""

import agenttrace


def test_version_string() -> None:
    assert agenttrace.__version__ == "0.1.0"

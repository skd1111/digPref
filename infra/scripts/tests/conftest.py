"""infra/scripts 工具脚本的测试引导：把脚本目录加入 sys.path。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

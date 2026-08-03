"""显式运行的 DeepSeek 真实调用验证入口。

运行前需要在进程环境中设置 DEEPSEEK_API_KEY。脚本只输出验证类别，不输出
密钥、请求头或完整供应商响应；自动化 pytest 不会导入或调用本脚本。
"""

from __future__ import annotations

import os
import sys

from self_react.deepseek import DeepSeekLLM
from self_react.llm import LLMError
from self_react.models import Message, MessageRole


def main() -> int:
    """执行一次最小真实请求并返回进程状态码。"""

    if not os.getenv("DEEPSEEK_API_KEY"):
        print(
            "manual verification skipped: DEEPSEEK_API_KEY is not set",
            file=sys.stderr,
        )
        return 2

    try:
        response = DeepSeekLLM().complete(
            [Message(role=MessageRole.USER, content="请只回答：ok")]
        )
    except LLMError as exc:
        print(f"manual verification failed: {type(exc).__name__}", file=sys.stderr)
        return 1

    if response.tool_calls:
        print(
            "manual verification succeeded: assistant_tool_calls="
            f"{len(response.tool_calls)}"
        )
    else:
        print("manual verification succeeded: assistant_message")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

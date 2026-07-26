"""冒烟测试公共配置：加载 .env；--print-result 控制是否打印响应。"""

import tests.helpers  # noqa: F401


def pytest_addoption(parser):
    parser.addoption(
        "--print-result",
        action="store_true",
        default=False,
        help="冒烟测试通过时打印 API 返回（需配合 -s）",
    )

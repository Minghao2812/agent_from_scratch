"""智谱 GLM 冒烟（备份文本模型）。默认关 thinking，省 token。"""

import pytest
from openai import OpenAI

from tests.helpers import env, require_env

pytestmark = pytest.mark.smoke


@pytest.fixture(scope="module")
def zhipu():
    cfg = require_env("ZHIPU_API_KEY")
    return OpenAI(
        api_key=cfg["ZHIPU_API_KEY"],
        base_url=env("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/"),
    )


def test_glm_chat(zhipu):
    model = env("ZHIPU_MODEL", "glm-5.2")
    resp = zhipu.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "1+1=? 只输出数字"}],
        max_tokens=8,
        temperature=0,
        extra_body={"thinking": {"type": "disabled"}},
    )
    text = (resp.choices[0].message.content or "").strip()
    assert text, "glm 返回空 content（若开了 thinking，请加大 max_tokens 或关闭 thinking）"
    assert "2" in text

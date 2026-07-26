"""DeepSeek 冒烟：flash（主力）+ pro（难点）。query 极短，省 token。"""

import pytest
from openai import OpenAI

from tests.helpers import env, require_env

pytestmark = pytest.mark.smoke


@pytest.fixture(scope="module")
def deepseek():
    cfg = require_env("DEEPSEEK_API_KEY")
    return OpenAI(
        api_key=cfg["DEEPSEEK_API_KEY"],
        base_url=env("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    )


def test_deepseek_flash(deepseek):
    model = env("DEEPSEEK_FLASH_MODEL", "deepseek-v4-flash")
    resp = deepseek.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "1+1=? 只输出数字"}],
        max_tokens=8,
        temperature=0,
        extra_body={"thinking": {"type": "disabled"}},
    )
    text = (resp.choices[0].message.content or "").strip()
    assert text, "flash 返回空 content"
    assert "2" in text


def test_deepseek_pro(deepseek):
    model = env("DEEPSEEK_PRO_MODEL", "deepseek-v4-pro")
    resp = deepseek.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "1+1=? 只输出数字"}],
        max_tokens=8,
        temperature=0,
        extra_body={"thinking": {"type": "disabled"}},
    )
    text = (resp.choices[0].message.content or "").strip()
    assert text, "pro 返回空 content"
    assert "2" in text

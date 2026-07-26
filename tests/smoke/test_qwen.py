"""阿里云 Qwen 冒烟：chat / VL / embedding / rerank。"""

import pytest
import requests
from openai import OpenAI

from tests.helpers import env, require_env

pytestmark = pytest.mark.smoke

# 官方样例图，仅用于 VL 联通性
_VL_IMAGE = "https://dashscope.oss-cn-beijing.aliyuncs.com/images/dog_and_girl.jpeg"


@pytest.fixture(scope="module")
def qwen():
    cfg = require_env("QWEN_API_KEY", "QWEN_BASE_URL")
    return OpenAI(api_key=cfg["QWEN_API_KEY"], base_url=cfg["QWEN_BASE_URL"])


def test_qwen_chat(qwen):
    model = env("QWEN_CHAT_MODEL", "qwen3.7-max")
    # 关闭 thinking，省 token、响应快
    resp = qwen.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "1+1=? 只输出数字"}],
        max_tokens=8,
        temperature=0,
        extra_body={"enable_thinking": False},
    )
    text = (resp.choices[0].message.content or "").strip()
    assert text, "qwen chat 返回空 content"
    assert "2" in text


def test_qwen_vl(qwen):
    model = env("QWEN_VL_MODEL", "qwen3-vl-plus")
    resp = qwen.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "一句话：图里有什么？"},
                {"type": "image_url", "image_url": {"url": _VL_IMAGE}},
            ],
        }],
        max_tokens=64,
    )
    text = (resp.choices[0].message.content or "").strip()
    assert text, "qwen-vl 返回空 content"


def test_qwen_embedding(qwen):
    model = env("QWEN_EMBEDDING_MODEL", "text-embedding-v4")
    resp = qwen.embeddings.create(
        model=model,
        input="1+1",
        dimensions=1024,
        encoding_format="float",
    )
    vec = resp.data[0].embedding
    assert len(vec) == 1024


def test_qwen_rerank():
    cfg = require_env("QWEN_API_KEY", "QWEN_RERANK_URL")
    model = env("QWEN_RERANK_MODEL", "qwen3-rerank")
    resp = requests.post(
        cfg["QWEN_RERANK_URL"],
        headers={
            "Authorization": f"Bearer {cfg['QWEN_API_KEY']}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "query": "1+1",
            "documents": ["1+1=2", "今天天气不错"],
            "top_n": 1,
        },
        timeout=60,
    )
    assert resp.status_code == 200, resp.text[:300]
    data = resp.json()
    results = data.get("results") or data.get("output", {}).get("results") or []
    assert results, f"rerank 无 results: {data}"
    assert results[0].get("index") == 0

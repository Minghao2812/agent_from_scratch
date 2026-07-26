"""博查 Bocha 联网检索冒烟。"""

import json

import pytest
import requests

from tests.helpers import env, require_env

pytestmark = pytest.mark.smoke


def test_bocha_web_search(request):
    cfg = require_env("BOCHA_API_KEY")
    base = env("BOCHA_BASE_URL", "https://api.bochaai.com").rstrip("/")
    url = f"{base}/v1/web-search"
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {cfg['BOCHA_API_KEY']}",
            "Content-Type": "application/json",
        },
        json={"query": "上海市天气", "summary": False, "count": 1},
        timeout=60,
    )
    assert resp.status_code == 200, resp.text[:300]
    data = resp.json()
    assert data.get("code") in (200, "200", 0, None), data
    pages = (data.get("data") or {}).get("webPages") or {}
    values = pages.get("value") or []
    assert values, f"无搜索结果: {data}"

    if request.config.getoption("--print-result"):
        print("\n[bocha] web-search result:")
        for i, page in enumerate(values, 1):
            print(f"  [{i}] {page.get('name')}")
            print(f"      url: {page.get('url')}")
            snippet = (page.get("snippet") or "")[:200]
            if snippet:
                print(f"      snippet: {snippet}")
        # 完整 JSON 方便对照字段
        print(json.dumps(data, ensure_ascii=False, indent=2))

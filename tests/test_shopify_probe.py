"""tests.test_shopify_probe —— 只读 ShopifyQL 客户端的守卫

**HTTP 200 不等于查到了。** Shopify 的四种失败形态长得几乎一样：

| 形态 | HTTP | 错误在哪 |
|---|---|---|
| 语法错 | **200** | `parseErrors` |
| 权限不足 | 200 | `errors[].extensions.code == ACCESS_DENIED` |
| 响应过大 | 200 | `errors[].extensions.code == RESPONSE_TOO_LARGE` |
| 被限流 | **200** | `errors[].extensions.code == THROTTLED` |

只看状态码的客户端，会把「语法错了」当成「查到了空数据」——
然后产出里写下一片 0。**「取不到」被写成了「观测值」**，
下游会把「没数据」读成「没有流量」。

这组测试逐个喂这四种形态，验它们**各自被认出来**。
还验两件容易被忽略的事：

- **分页**：行数撞顶要继续翻——漏页 = 少算流量，而且不会报错；
- **凭证不出现在任何错误消息里**（凭证进日志 = 凭证进别人的聊天记录）。
"""

from __future__ import annotations

import http.client
import json
import urllib.error
from typing import Any

import pytest

from boxes.shopify_snapshot.shopifyql_client import (
    ShopifyQLClient,
    ShopifyQLError,
    ShopifyQLParseError,
    ShopifyQLPermissionError,
    ShopifyQLThrottledError,
    ShopifyQLTooLargeError,
)
from scripts.probe_shopifyql import probe


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw


def _ok_payload(rows: list[dict[str, Any]], columns: list[str] | None = None) -> dict[str, Any]:
    return {
        "data": {
            "shopifyqlQuery": {
                "tableData": {
                    "columns": columns or [{"name": "sessions", "dataType": "INTEGER"}],
                    "rows": rows,
                },
                "parseErrors": [],
                "parseWarnings": [],
            }
        },
        "extensions": {
            "shopifyqlCost": {
                "requestedQueryCost": 1,
                "maximumAvailable": 1000,
                "currentlyAvailable": 999,
                "windowResetAt": "2026-07-16T07:06:00+00:00",
            }
        },
    }


def _client(payloads: list[dict[str, Any]], **kwargs: Any) -> tuple[ShopifyQLClient, list[str]]:
    """假客户端：按顺序回放 payload，并记录每次发出去的语句"""
    sent: list[str] = []
    queue = list(payloads)

    def opener(request: Any, timeout: float) -> _FakeResponse:
        body = json.loads(request.data.decode("utf-8"))
        sent.append(body["variables"]["statement"])
        return _FakeResponse(queue.pop(0))

    client = ShopifyQLClient(
        store_domain="giftsjourneyrebirth.myshopify.com",
        token="shpat_secret_token_value",
        api_version="2026-01",
        opener=opener,
        **{"sleeper": lambda _s: None, **kwargs},
    )
    return client, sent


# ============================================================
# 四种失败形态
# ============================================================


class TestTheFourFailureShapes:
    def test_a_parse_error_is_not_an_empty_result(self):
        """语法错：HTTP 200，错误在 parseErrors 里

        **不能当成「查到了 0 行」**——那会把「没查成」写成「没有流量」。
        """
        client, _sent = _client([{
            "data": {
                "shopifyqlQuery": {
                    "tableData": None,
                    "parseErrors": ["Column Not Found: Column 'total_revenue' not found"],
                }
            }
        }])
        with pytest.raises(ShopifyQLParseError) as exc:
            client.query("FROM sessions SHOW total_revenue")
        assert "total_revenue" in str(exc.value)

    def test_a_permission_error_says_what_is_missing(self):
        """权限不足要说清缺什么——不然只能靠猜"""
        client, _sent = _client([{
            "data": None,
            "errors": [{
                "message": "Access denied for shopifyqlQuery field.",
                "extensions": {"code": "ACCESS_DENIED"},
            }],
        }])
        with pytest.raises(ShopifyQLPermissionError) as exc:
            client.query("FROM sessions SHOW sessions")
        message = str(exc.value)
        assert "read_reports" in message
        assert "Level 2" in message

    def test_a_too_large_response_is_not_retried_blindly(self):
        """响应过大：**等也没用**，必须把查询改小"""
        client, _sent = _client([{
            "data": {"shopifyqlQuery": None},
            "errors": [{
                "message": "The query returned too much data to be delivered.",
                "extensions": {"code": "RESPONSE_TOO_LARGE"},
            }],
        }])
        with pytest.raises(ShopifyQLTooLargeError) as exc:
            client.query("FROM sessions SHOW sessions GROUP BY landing_page_path")
        assert "等也没用" in str(exc.value)

    def test_a_throttled_query_waits_for_the_window(self):
        """被限流：等 windowResetAt 再试——不是立刻重试"""
        waited: list[float] = []
        client, _sent = _client([{
            "data": {"shopifyqlQuery": None},
            "errors": [{
                "message": "Rate limited. Please retry later.",
                "extensions": {
                    "code": "THROTTLED",
                    "cost": {
                        "requestedQueryCost": 5,
                        "maximumAvailable": 1000,
                        "currentlyAvailable": 0,
                        "windowResetAt": "2020-01-01T00:00:00+00:00",
                    },
                },
            }],
        }], sleeper=waited.append)
        with pytest.raises(ShopifyQLThrottledError):
            client.query("FROM sessions SHOW sessions")
        assert waited, "被限流时应该等到预算恢复——不等待就是立刻重试"

    def test_a_null_table_is_a_failure_not_an_empty_table(self):
        """tableData 是 null 且没报错 → 这不是「查到了 0 行」"""
        client, _sent = _client([{
            "data": {"shopifyqlQuery": {"tableData": None, "parseErrors": []}},
        }])
        with pytest.raises(ShopifyQLError) as exc:
            client.query("FROM sessions SHOW sessions")
        assert "null" in str(exc.value)

    def test_an_unknown_graphql_error_still_raises(self):
        """不认识的错误码也要报错——不能当成功"""
        client, _sent = _client([{
            "data": None,
            "errors": [{"message": "boom", "extensions": {"code": "SOMETHING_NEW"}}],
        }])
        with pytest.raises(ShopifyQLError):
            client.query("FROM sessions SHOW sessions")


# ============================================================
# 分页
# ============================================================


class TestPagination:
    def test_a_full_page_triggers_the_next_page(self):
        """行数撞顶要继续翻——漏页 = 少算流量，而且不会报错"""
        first = _ok_payload([{"sessions": 1} for _ in range(250)])
        second = _ok_payload([{"sessions": 2}])
        client, sent = _client([first, second])

        result = client.query("FROM sessions SHOW sessions GROUP BY landing_page_path")

        assert len(result.rows) == 251, f"只取回 {len(result.rows)} 行"
        assert result.pages_fetched == 2
        assert "LIMIT 250 OFFSET 0" in sent[0]
        assert "LIMIT 250 OFFSET 250" in sent[1]

    def test_a_partial_page_ends_the_pagination(self):
        """不满一页就停——不多发一次请求"""
        client, sent = _client([_ok_payload([{"sessions": 1}])])
        result = client.query("FROM sessions SHOW sessions")
        assert len(sent) == 1
        assert result.pages_fetched == 1


# ============================================================
# 凭证
# ============================================================


class TestTheTokenNeverLeaks:
    def test_the_token_is_not_in_the_result(self):
        client, _sent = _client([_ok_payload([{"sessions": 1}])])
        result = client.query("FROM sessions SHOW sessions")
        assert "shpat_secret_token_value" not in json.dumps(
            {"columns": result.columns, "rows": result.rows}, default=str
        )

    def test_the_token_is_not_in_the_error_message(self):
        """错误消息会进日志、进聊天记录——凭证不能跟着走"""
        client, _sent = _client([{
            "data": None,
            "errors": [{
                "message": "Access denied for shopifyqlQuery field.",
                "extensions": {"code": "ACCESS_DENIED"},
            }],
        }])
        with pytest.raises(ShopifyQLPermissionError) as exc:
            client.query("FROM sessions SHOW sessions")
        assert "shpat_secret_token_value" not in str(exc.value)


# ============================================================
# 探测脚本
# ============================================================


class TestTheProbe:
    def test_one_failure_does_not_stop_the_others(self):
        """一条挂掉不影响别的——探测要的是**全清单**，不是第一手结果"""
        client, _sent = _client([
            {"data": {"shopifyqlQuery": {"tableData": None,
                                          "parseErrors": ["boom"]}}},
            _ok_payload([{"sessions": 168}]),
        ])
        results = probe(client, {"totals": "FROM sessions SHOW sessions",
                                 "landing": "FROM sessions SHOW sessions"})

        assert len(results) == 2
        assert results[0].ok is False and results[0].kind == "parse"
        assert results[1].ok is True and results[1].row_count == 1

    def test_the_window_excludes_today(self):
        """窗口不含当天——当天的数会随着新订单一直涨

        基线要能复现，就不能含当天未走完的部分。
        """
        from datetime import date, timedelta

        from scripts.probe_shopifyql import _window

        start, end = _window(28)
        assert end == (date.today() - timedelta(days=1)).isoformat()
        assert start == (date.today() - timedelta(days=28)).isoformat()


# ============================================================
# 瞬时断连：店面会抽风，不能一次断连就打死一整轮
# ============================================================


class TestTheTransientDisconnect:
    def test_it_retries_after_an_incomplete_read(self):
        """**断连不是「这轮做不成」**——过两秒再来一次就好

        实测：一次 `IncompleteRead` 打死了一整轮快照，
        而那只是店面抖了一下。
        """
        calls: list[int] = []
        waits: list[float] = []

        def opener(request: Any, timeout: float) -> _FakeResponse:
            calls.append(1)
            if len(calls) == 1:
                raise http.client.IncompleteRead(b"")
            return _FakeResponse(_ok_payload([{"sessions": 168}]))

        client = ShopifyQLClient(
            store_domain="giftsjourneyrebirth.myshopify.com",
            token="shpat_secret_token_value",
            api_version="2026-01",
            opener=opener,
            sleeper=waits.append,
        )
        result = client.query("FROM sessions SHOW sessions")
        assert result.rows == ({"sessions": 168},)
        assert len(calls) == 2
        assert waits and waits[0] > 0, "重试之间要退避，别把店面打得更狠"

    def test_it_gives_up_after_the_retries_and_says_so(self):
        """退避用完还断 → **失败要说清试了几次**"""
        calls: list[int] = []

        def opener(request: Any, timeout: float) -> _FakeResponse:
            calls.append(1)
            raise http.client.IncompleteRead(b"")

        client = ShopifyQLClient(
            store_domain="giftsjourneyrebirth.myshopify.com",
            token="shpat_secret_token_value",
            api_version="2026-01",
            opener=opener,
            sleeper=lambda _s: None,
        )
        with pytest.raises(ShopifyQLError) as exc:
            client.query("FROM sessions SHOW sessions")
        assert "重试" in str(exc.value)
        assert len(calls) == 4      # 1 次 + 3 次重试

    def test_a_permission_error_is_not_retried(self):
        """**401/403 是权限问题**——重试一万次也一样"""
        calls: list[int] = []

        def opener(request: Any, timeout: float) -> _FakeResponse:
            calls.append(1)
            raise urllib.error.HTTPError(
                request.full_url, 403, "Forbidden", {}, None)

        client = ShopifyQLClient(
            store_domain="giftsjourneyrebirth.myshopify.com",
            token="shpat_secret_token_value",
            api_version="2026-01",
            opener=opener,
            sleeper=lambda _s: None,
        )
        with pytest.raises(ShopifyQLPermissionError):
            client.query("FROM sessions SHOW sessions")
        assert len(calls) == 1, "权限错误不该重试"

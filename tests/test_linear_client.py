import json
import unittest

import httpx
from temporalio.exceptions import ApplicationError

from software_factory.linear.client import LinearClient

QUERY = "query { viewer { id } }"


def make_client(response: httpx.Response, requests: list[httpx.Request]) -> LinearClient:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response

    return LinearClient("lin-key", transport=httpx.MockTransport(handler))


class TestLinearClient(unittest.IsolatedAsyncioTestCase):
    async def execute_error(self, response: httpx.Response) -> ApplicationError:
        client = make_client(response, [])
        with self.assertRaises(ApplicationError) as ctx:
            await client.execute(QUERY, {})
        return ctx.exception

    async def test_sends_query_with_api_key(self) -> None:
        requests: list[httpx.Request] = []
        client = make_client(httpx.Response(200, json={"data": {"viewer": {"id": "u1"}}}), requests)
        data = await client.execute(QUERY, {"a": 1})
        self.assertEqual(data, {"viewer": {"id": "u1"}})
        request = requests[0]
        self.assertEqual(str(request.url), "https://api.linear.app/graphql")
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.headers["Authorization"], "lin-key")
        self.assertEqual(json.loads(request.content), {"query": QUERY, "variables": {"a": 1}})

    async def test_graphql_error_is_non_retryable(self) -> None:
        error = await self.execute_error(
            httpx.Response(200, json={"errors": [{"message": "Entity not found"}]})
        )
        self.assertTrue(error.non_retryable)
        self.assertIn("Entity not found", str(error))

    async def test_rate_limit_is_retryable(self) -> None:
        error = await self.execute_error(
            httpx.Response(
                400,
                json={
                    "errors": [{"message": "rate limited", "extensions": {"code": "RATELIMITED"}}]
                },
            )
        )
        self.assertFalse(error.non_retryable)

    async def test_unauthorized_is_non_retryable(self) -> None:
        error = await self.execute_error(httpx.Response(401, text="Unauthorized"))
        self.assertTrue(error.non_retryable)

    async def test_server_error_is_retryable(self) -> None:
        error = await self.execute_error(httpx.Response(502, text="Bad Gateway"))
        self.assertFalse(error.non_retryable)

    async def test_server_error_with_graphql_errors_is_retryable(self) -> None:
        error = await self.execute_error(
            httpx.Response(
                500,
                json={
                    "errors": [
                        {"message": "boom", "extensions": {"code": "INTERNAL_SERVER_ERROR"}}
                    ]
                },
            )
        )
        self.assertFalse(error.non_retryable)


if __name__ == "__main__":
    unittest.main()

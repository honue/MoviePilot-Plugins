"""验证请求缓存的失败恢复，不向 Bangumi 写入数据。"""

import ast
from functools import wraps
from pathlib import Path
import unittest
from unittest.mock import Mock


def cached(**options):
    """独立测试使用与宿主一致的 None/异常跳过语义。"""
    def decorate(func):
        values = {}
        @wraps(func)
        def wrapped(self, method, *args, **kwargs):
            key = (method, args, tuple(sorted(kwargs.items())))
            if key not in values:
                value = func(self, method, *args, **kwargs)
                if value is not None:
                    values[key] = value
                return value
            return values[key]
        return wrapped
    return decorate


def load_client():
    path = Path(__file__).resolve().parents[1] / "plugins/bangumisync/__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "BangumiAPIClient")
    cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__cached_invoke"]
    namespace = {"cached": cached, "ImmediateException": RuntimeError, "__name__": __name__}
    exec(compile(ast.Module(body=[cls], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["BangumiAPIClient"]()


def response(status=200, invalid=False):
    result = Mock(status_code=status)
    result.json.side_effect = ValueError("invalid") if invalid else None
    result.json.return_value = {"data": []}
    return result


class CacheTests(unittest.TestCase):
    def check_recovery(self, failure, error=None):
        client = load_client()
        success = response()
        request = Mock(side_effect=[failure, success])
        client.req_method = {"get": request}
        invoke = client._BangumiAPIClient__cached_invoke
        if error:
            with self.assertRaises(error):
                invoke("get", url="test")
        else:
            self.assertIsNone(invoke("get", url="test"))
        self.assertIs(invoke("get", url="test"), success)
        self.assertIs(invoke("get", url="test"), success)
        self.assertEqual(request.call_count, 2)

    def test_http_failures_are_not_cached(self):
        for status in (400, 401, 403, 404, 429, 500, 502, 503):
            with self.subTest(status=status):
                self.check_recovery(response(status), RuntimeError)

    def test_invalid_json_is_not_cached(self):
        self.check_recovery(response(invalid=True), RuntimeError)

    def test_no_response_is_not_cached(self):
        self.check_recovery(None)

    def test_connection_exception_is_not_cached(self):
        self.check_recovery(ConnectionError("offline"), ConnectionError)

    def test_success_is_cached(self):
        for status in (200, 201, 202, 204):
            with self.subTest(status=status):
                client = load_client()
                result = response(status, invalid=status in (202, 204))
                request = Mock(return_value=result)
                client.req_method = {"get": request}
                for _ in range(2):
                    self.assertIs(client._BangumiAPIClient__cached_invoke("get", url="test"), result)
                self.assertEqual(request.call_count, 1)
                if status in (202, 204):
                    result.json.assert_not_called()


if __name__ == "__main__":
    unittest.main()

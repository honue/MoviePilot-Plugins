"""验证访问失败与条目失败分类，不发出网络请求。"""

import ast
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


def load_method(filename, name, namespace):
    path = Path(__file__).resolve().parents[1] / "plugins/doubanwatching" / filename
    tree = ast.parse(path.read_text(encoding="utf-8"))
    method = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == name)
    method.returns = None
    for arg in method.args.args:
        arg.annotation = None
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


class AccessTests(unittest.TestCase):
    def status_request(self, status=200, payload=None, error=None):
        response = SimpleNamespace(status_code=status, json=lambda: payload, text="response")
        post = Mock(return_value=response, side_effect=error)
        helper = SimpleNamespace(headers={}, cookies={}, ck="test", unavailable=False)
        method = load_method("DoubanHelper.py", "set_watching_status",
                             {"requests": SimpleNamespace(post=post), "logger": Mock()})
        result = method(helper, "123")
        return result, helper.unavailable, post

    def test_http403_stops_without_reposting(self):
        result, unavailable, post = self.status_request(403)
        self.assertFalse(result)
        self.assertTrue(unavailable)
        post.assert_called_once()

    def test_connection_error_is_access_failure(self):
        result, unavailable, post = self.status_request(error=ConnectionError("offline"))
        self.assertFalse(result)
        self.assertTrue(unavailable)
        post.assert_called_once()

    def test_json403_is_access_failure(self):
        result, unavailable, post = self.status_request(payload={"r": 1, "code": 403})
        self.assertFalse(result)
        self.assertTrue(unavailable)
        post.assert_called_once()

    def test_boolean_false_is_item_failure_not_success(self):
        result, unavailable, _ = self.status_request(payload={"r": False})
        self.assertFalse(result)
        self.assertFalse(unavailable)

    def test_zero_is_success(self):
        result, unavailable, _ = self.status_request(payload={"r": 0})
        self.assertTrue(result)
        self.assertFalse(unavailable)

    def test_search_access_failures_return_safe_pair(self):
        for response in (None, SimpleNamespace(status_code=403)):
            with self.subTest(response=response):
                helper = SimpleNamespace(headers={}, unavailable=False)
                get_res = Mock(return_value=response)
                method = load_method("DoubanHelper.py", "get_subject_id", {
                    "requests": SimpleNamespace(RequestException=ConnectionError),
                    "RequestUtils": lambda **kwargs: SimpleNamespace(get_res=get_res),
                    "logger": Mock(),
                })
                self.assertEqual(method(helper, title="test"), (None, None))
                self.assertTrue(helper.unavailable)
                get_res.assert_called_once()

    def test_plugin_preserves_failure_classification(self):
        for unavailable in (False, True):
            with self.subTest(unavailable=unavailable):
                helper = SimpleNamespace(
                    unavailable=unavailable,
                    get_subject_id=lambda **kwargs: ("title", "123"),
                    set_watching_status=lambda **kwargs: False,
                )
                method = load_method("__init__.py", "_sync_to_douban", {
                    "DoubanHelper": lambda **kwargs: helper,
                    "logger": Mock(), "datetime": datetime,
                })
                plugin = SimpleNamespace(_cookie="", _private=True, _wait_process={}, save_data=Mock())
                result = method(plugin, "title", "do", "TV", {}, "poster")
                self.assertIs(result, None if unavailable else False)
                self.assertIn("title", plugin._wait_process)


if __name__ == "__main__":
    unittest.main()

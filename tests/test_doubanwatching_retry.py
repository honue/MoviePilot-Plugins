"""验证补同步遍历期间删除成功条目，不请求豆瓣或改动实际数据。"""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class RetryTests(unittest.TestCase):
    def test_successful_retries_can_remove_pending_entries(self):
        path = Path(__file__).resolve().parents[1] / "plugins/doubanwatching/__init__.py"
        for failed_titles in (set(), {"待同步B"}):
            with self.subTest(failed_titles=failed_titles):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                method = next(node for node in ast.walk(tree)
                              if isinstance(node, ast.FunctionDef)
                              and node.name == "_process_tv_show")
                method.returns = None
                for arg in method.args.args:
                    arg.annotation = None
                namespace = {"MetaInfo": lambda title: SimpleNamespace(),
                             "MediaType": lambda value: value, "logger": Mock()}
                exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"),
                     namespace)
                pending = {title: {"status": "do", "type": "TV", "poster_path": "poster"}
                           for title in ("待同步A", "待同步B", "待同步C")}
                calls = []
                plugin = SimpleNamespace(
                    _first=False,
                    _recognize_media=Mock(return_value=SimpleNamespace(
                        seasons={1: list(range(12))}, poster_path="poster")),
                    format_title=lambda title, season: title,
                    get_data=lambda key: pending,
                )

                def sync(title, status, media_type, processed, poster):
                    calls.append(title)
                    if title in failed_titles:
                        return False
                    # 与实际成功分支一致：同步后从同一个待处理字典中删除条目。
                    if title in plugin._wait_process:
                        del plugin._wait_process[title]
                    return True

                plugin._wait_process = pending
                plugin._sync_to_douban = sync
                event = SimpleNamespace(item_name="当前剧集 S01E02", season_id=1,
                                        episode_id=2, tmdb_id=123, item_type="TV")
                namespace["_process_tv_show"](plugin, event, {})
                self.assertEqual(calls, ["当前剧集", "待同步A", "待同步B", "待同步C"])
                self.assertEqual(set(pending), failed_titles)


if __name__ == "__main__":
    unittest.main()

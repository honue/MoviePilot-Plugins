"""验证正常跳过、条目失败及访问异常的补同步行为，不请求豆瓣。"""

import ast
from pathlib import Path
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock


def make_plugin(pending, outcomes=None):
    path = Path(__file__).resolve().parents[1] / "plugins/doubanwatching/__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {"_process_tv_show", "_process_movie", "_retry_pending"}
    methods = [node for node in ast.walk(tree)
               if isinstance(node, ast.FunctionDef) and node.name in names]
    for method in methods:
        method.returns = None
        for arg in method.args.args:
            arg.annotation = None
    namespace = {"MetaInfo": lambda title: SimpleNamespace(),
                 "MediaType": lambda value: value, "logger": Mock()}
    exec(compile(ast.Module(body=methods, type_ignores=[]), str(path), "exec"), namespace)
    plugin = SimpleNamespace(
        _first=False, _wait_process=pending,
        _recognize_media=Mock(return_value=SimpleNamespace(
            seasons={1: list(range(12))}, poster_path="poster")),
        format_title=lambda title, season: title,
        get_data=lambda key: pending,
    )
    for name in names:
        setattr(plugin, name, MethodType(namespace[name], plugin))
    calls = []
    outcomes = outcomes or {}

    def sync(title, status, media_type, processed, poster):
        calls.append(title)
        result = outcomes.get(title, True)
        if result is True and title in plugin._wait_process:
            del plugin._wait_process[title]
        return result

    plugin._sync_to_douban = sync
    return plugin, calls


def pending_items(*titles):
    return {title: {"status": "do", "type": "TV", "poster_path": "poster"}
            for title in titles}


def tv_event():
    return SimpleNamespace(item_name="当前剧集 S01E02", season_id=1,
                           episode_id=2, tmdb_id=123, item_type="TV")


class RetryTests(unittest.TestCase):
    def test_successful_retries_can_remove_pending_entries(self):
        for outcomes in ({}, {"B": False}):
            with self.subTest(outcomes=outcomes):
                pending = pending_items("A", "B", "C")
                plugin, calls = make_plugin(pending, outcomes)
                plugin._process_tv_show(tv_event(), {})
                self.assertEqual(calls, ["当前剧集", "A", "B", "C"])
                self.assertEqual(set(pending), set(outcomes))

    def test_already_synced_tv_still_retries_pending(self):
        plugin, calls = make_plugin(pending_items("A"))
        plugin._process_tv_show(tv_event(), {"当前剧集": {"subject_id": "123"}})
        self.assertEqual(calls, ["A"])

    def test_already_synced_movie_still_retries_pending(self):
        plugin, calls = make_plugin(pending_items("A"))
        event = SimpleNamespace(item_name="当前电影", tmdb_id=123, item_type="MOV")
        plugin._process_movie(event, {"当前电影": {"subject_id": "123"}})
        self.assertEqual(calls, ["A"])

    def test_current_item_failure_does_not_block_other_items_or_retry_itself(self):
        plugin, calls = make_plugin(pending_items("当前剧集", "A"), {"当前剧集": False})
        plugin._process_tv_show(tv_event(), {})
        self.assertEqual(calls, ["当前剧集", "A"])
        self.assertEqual(list(plugin._wait_process), ["当前剧集"])

    def test_current_access_failure_does_not_start_batch(self):
        plugin, calls = make_plugin(pending_items("A"), {"当前剧集": None})
        plugin._process_tv_show(tv_event(), {})
        self.assertEqual(calls, ["当前剧集"])
        self.assertEqual(list(plugin._wait_process), ["A"])

    def test_batch_stops_on_access_failure_and_preserves_remaining(self):
        pending = pending_items("A", "B", "C")
        plugin, calls = make_plugin(pending, {"B": None})
        plugin._retry_pending({})
        self.assertEqual(calls, ["A", "B"])
        self.assertEqual(list(pending), ["B", "C"])


if __name__ == "__main__":
    unittest.main()

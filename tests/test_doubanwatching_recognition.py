"""独立验证 V2/V3 识别边界，不启动 MoviePilot 或请求豆瓣。"""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


def load_recognize_method(chain, version_flag="v3"):
    # 只载入待测方法，避免导入插件时依赖 MoviePilot 的完整运行环境。
    path = Path(__file__).resolve().parents[1] / "plugins/doubanwatching/__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    plugin = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    method = next(node for node in plugin.body
                  if isinstance(node, ast.FunctionDef) and node.name == "_recognize_media")
    method.returns = None
    for arg in method.args.args:
        arg.annotation = None
    module = ast.Module(body=[method], type_ignores=[])
    namespace = {"MediaChain": lambda: chain,
                 "settings": SimpleNamespace(VERSION_FLAG=version_flag)}
    exec(compile(module, str(path), "exec"), namespace)
    return namespace["_recognize_media"]


class RecognitionTests(unittest.TestCase):
    def invoke(self, recognize, meta, tmdb_id, version_flag="v3"):
        chain = SimpleNamespace(recognize_media=recognize)
        return load_recognize_method(chain, version_flag)(None, meta, tmdb_id)

    def test_v2_keeps_legacy_tmdbid(self):
        result = object()
        calls = []

        def recognize(meta=None, mtype=None, tmdbid=None, cache=True):
            calls.append((meta, mtype, tmdbid, cache))
            return result

        meta = SimpleNamespace(type="电视剧")
        self.assertIs(self.invoke(recognize, meta, 74277, "v2"), result)
        self.assertEqual(calls, [(meta, "电视剧", 74277, True)])

    def test_v3_passes_paired_source_and_string_id(self):
        calls = []

        def recognize(meta=None, mtype=None, media_source=None, media_id=None, cache=True):
            calls.append((mtype, media_source, media_id, cache))
            return "matched"

        for media_type in ("电影", "电视剧"):
            with self.subTest(media_type=media_type):
                meta = SimpleNamespace(type=media_type)
                self.assertEqual(self.invoke(recognize, meta, 74277), "matched")
                self.assertEqual(calls[-1], (media_type, "themoviedb", "74277", True))

    def test_v2_title_retry_clears_backfilled_id(self):
        meta = SimpleNamespace(type="电影", tmdbid=74277)

        def recognize(meta=None, mtype=None, tmdbid=None, cache=True):
            self.assertIsNone(tmdbid)
            self.assertIsNone(meta.tmdbid)
            return "title matched"

        self.assertEqual(self.invoke(recognize, meta, None, "v2"), "title matched")

    def test_v3_title_retry_clears_backfilled_identity(self):
        meta = SimpleNamespace(type="电视剧")
        calls = []

        def recognize(meta=None, mtype=None, media_source=None, media_id=None, cache=True):
            calls.append((media_source, media_id))
            if media_id:
                meta.media_source, meta.media_id = media_source, media_id
                return None
            self.assertIsNone(meta.media_source)
            self.assertIsNone(meta.media_id)
            return "title matched"

        self.assertIsNone(self.invoke(recognize, meta, 74277))
        self.assertEqual(self.invoke(recognize, meta, None), "title matched")
        self.assertEqual(calls, [("themoviedb", "74277"), (None, None)])

    def test_v3_without_id_uses_title(self):
        def recognize(meta=None, mtype=None, media_source=None, media_id=None, cache=True):
            self.assertIsNone(media_source)
            self.assertIsNone(media_id)
            return "title matched"

        self.assertEqual(self.invoke(recognize, SimpleNamespace(type="电影"), None),
                         "title matched")

    def test_internal_typeerror_is_not_retried(self):
        calls = Mock()

        def recognize(meta=None, mtype=None, media_source=None, media_id=None, cache=True):
            calls()
            raise TypeError("recognition failed internally")

        with self.assertRaisesRegex(TypeError, "recognition failed internally"):
            self.invoke(recognize, SimpleNamespace(type="电影"), 74277)
        calls.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

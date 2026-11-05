"""独立验证 V2/V3 媒体身份、订阅查询和 Webhook 日志，不写入 Bangumi。"""

import ast
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch


def load_plugin(version="v3", **overrides):
    path = Path(__file__).resolve().parents[1] / "plugins/bangumisync/__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "BangumiSync")
    names = {"parse_event_meta", "_get_tmdb_id", "hook", "get_original_language_tmdb_episodes"}
    cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
    cls.bases = []
    for method in cls.body:
        method.decorator_list = [d for d in method.decorator_list
                                 if isinstance(d, ast.Name) and d.id == "staticmethod"]
        method.returns = None
        for arg in method.args.args:
            arg.annotation = None
    media_type = SimpleNamespace(TV="TV", MOVIE="MOV")
    def meta_info(path):
        meta = SimpleNamespace(name="test", year=None, season_episode="", media_id=None)
        meta.set_season = lambda season: None
        meta.set_episode = lambda episode: None
        return meta
    namespace = {"settings": SimpleNamespace(VERSION_FLAG=version), "logger": Mock(),
                 "MetaInfoPath": meta_info, "MediaType": media_type,
                 "MetaBase": object, "WebhookEventInfo": object, "Path": Path,
                 "MediaServerChain": Mock(return_value=SimpleNamespace(iteminfo=Mock(return_value=None))),
                 "MediaServerItem": Mock(), "ImmediateException": RuntimeError}
    namespace.update(overrides)
    exec(compile(ast.Module(body=[cls], type_ignores=[]), str(path), "exec"), namespace)
    plugin = namespace['BangumiSync']()
    plugin.get_itemid = lambda event: "series-1"
    return plugin, namespace


def event(media_type="Episode"):
    return SimpleNamespace(item_path="", item_name="test", season_id=1, episode_id=2,
                           media_type=media_type, tmdb_id=999, server_name="emby",
                           channel="emby", event="playback.start", percentage=0, user_name="user")


class CompatibilityTests(unittest.TestCase):
    def test_v2_uses_legacy_media_server_id(self):
        plugin, ns = load_plugin("v2")
        ns['MediaServerChain'].return_value.iteminfo.return_value = SimpleNamespace(tmdbid=123)
        meta = plugin.parse_event_meta(event())
        self.assertEqual(meta.tmdbid, 123)
        ns['MediaServerItem'].get_by_itemid.assert_not_called()

    def test_v3_uses_paired_media_server_identity(self):
        plugin, ns = load_plugin()
        ns['MediaServerChain'].return_value.iteminfo.return_value = SimpleNamespace(
            media_source="themoviedb", media_id="123")
        meta = plugin.parse_event_meta(event())
        self.assertEqual((meta.media_source, meta.media_id), ("themoviedb", "123"))
        self.assertFalse(hasattr(meta, 'tmdbid'))

    def test_v3_does_not_treat_other_source_as_tmdb(self):
        plugin, _ = load_plugin()
        self.assertIsNone(plugin._get_tmdb_id(SimpleNamespace(media_source="bangumi", media_id="123")))
        self.assertIsNone(plugin._get_tmdb_id(None))

    def test_movie_event_id_has_priority_on_both_versions(self):
        for version in ('v2', 'v3'):
            with self.subTest(version=version):
                plugin, ns = load_plugin(version)
                meta = plugin.parse_event_meta(event('Movie'))
                actual = meta.media_id if version == 'v3' else meta.tmdbid
                self.assertEqual(str(actual), '999')
                ns['MediaServerChain'].assert_not_called()

    def test_v3_local_fallback_uses_scoped_session_and_server(self):
        plugin, ns = load_plugin()
        ns['MediaServerItem'].get_by_server_itemid.return_value = SimpleNamespace(
            media_source='themoviedb', media_id='456')
        session_module = ModuleType('app.db.session')
        db = object()
        session_module.SessionFactory = Mock(return_value=nullcontext(db))
        with patch.dict(sys.modules, {'app.db.session': session_module}):
            meta = plugin.parse_event_meta(event())
        self.assertEqual(meta.media_id, '456')
        ns['MediaServerItem'].get_by_server_itemid.assert_called_once_with(
            db=db, server='emby', item_id='series-1')

    def test_v2_local_fallback_keeps_old_query(self):
        plugin, ns = load_plugin('v2')
        ns['MediaServerItem'].get_by_itemid.return_value = SimpleNamespace(tmdbid=456)
        self.assertEqual(plugin.parse_event_meta(event()).tmdbid, 456)
        ns['MediaServerItem'].get_by_itemid.assert_called_once_with(db=None, item_id='series-1')

    def test_v3_without_id_preserves_title_fallback(self):
        plugin, ns = load_plugin()
        ns['MediaServerItem'].get_by_server_itemid.return_value = None
        module = ModuleType('app.db.session')
        module.SessionFactory = lambda: nullcontext(object())
        with patch.dict(sys.modules, {'app.db.session': module}):
            meta = plugin.parse_event_meta(event())
        self.assertEqual((meta.media_source, meta.media_id), (None, None))

    def test_hook_uses_version_specific_recognition(self):
        for version in ('v2', 'v3'):
            with self.subTest(version=version):
                plugin, ns = load_plugin(version)
                plugin._enable, plugin._user, plugin._notify = True, 'user', False
                meta = SimpleNamespace(media_id='123')
                plugin.parse_event_meta = Mock(return_value=meta)
                plugin.chain = SimpleNamespace(recognize_media=Mock(return_value=SimpleNamespace(
                    genre_ids=[16], type='TV')))
                plugin._match_tv_subject = Mock(return_value=(1, 2, False))
                plugin.sync_subject_status = Mock()
                plugin.hook(SimpleNamespace(event_data=event()))
                if version == 'v3':
                    plugin.chain.recognize_media.assert_called_once_with(
                        meta=meta, media_source='themoviedb', media_id='123')
                else:
                    plugin.chain.recognize_media.assert_called_once_with(meta)
                plugin.sync_subject_status.assert_called_once_with(1, 2, False)
                ns['logger'].error.assert_not_called()

    def test_webhook_log_does_not_serialize_full_event(self):
        plugin, ns = load_plugin()
        plugin._enable, plugin._user = True, 'another-user'
        info = event()
        info.item_name = 'title\n' + 'x' * 10000
        info.json_object = {'private_payload': 'MUST_NOT_LOG' * 10000}
        plugin.hook(SimpleNamespace(event_data=info))
        logged = ns['logger'].debug.call_args.args[0]
        self.assertLess(len(logged), 350)
        self.assertNotIn('MUST_NOT_LOG', logged)
        self.assertNotIn('\n', logged)

    def test_episode_group_subscription_query_on_both_versions(self):
        for version in ('v2', 'v3'):
            with self.subTest(version=version):
                plugin, _ = load_plugin(version)
                oper = Mock()
                oper.list_by_media_identity.return_value = [
                    SimpleNamespace(season=2, episode_group='wrong'),
                    SimpleNamespace(season=1, episode_group='group')]
                oper.list_by_tmdbid.return_value = [SimpleNamespace(episode_group='group')]
                module = ModuleType('app.db.subscribe_oper')
                module.SubscribeOper = lambda: oper
                tmdb = SimpleNamespace(tmdb=SimpleNamespace(tv=SimpleNamespace(
                    group_episodes=Mock(return_value=[{'order': 1, 'episodes': [{'id': 10}]}]))))
                plugin.chain = SimpleNamespace(
                    modulemanager=SimpleNamespace(get_running_module=lambda name: tmdb),
                    tmdb_info=Mock(return_value=None))
                plugin.temporary_attributes = lambda *args, **kwargs: nullcontext()
                media = SimpleNamespace(original_language='ja', tmdb_id=123, type='TV', episode_groups=[])
                with patch.dict(sys.modules, {'app.db.subscribe_oper': module}):
                    self.assertEqual(plugin.get_original_language_tmdb_episodes(media, 1), [{'id': 10}])
                if version == 'v3':
                    oper.list_by_media_identity.assert_called_once_with(media_source='themoviedb', media_id='123')
                    oper.list_by_tmdbid.assert_not_called()
                else:
                    oper.list_by_tmdbid.assert_called_once_with(123, 1)


if __name__ == '__main__':
    unittest.main()

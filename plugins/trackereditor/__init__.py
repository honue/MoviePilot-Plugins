from typing import List, Tuple, Dict, Any, Union
from app.log import logger
from app.modules.qbittorrent import Qbittorrent
from qbittorrentapi.torrents import TorrentInfoList
from app.modules.transmission import Transmission
from transmission_rpc.torrent import Torrent
from app.plugins import _PluginBase


class TrackerEditor(_PluginBase):
    # 插件名称
    plugin_name = "Tracker替换"
    # 插件描述
    plugin_desc = "批量replace种子的tracker qb 4.6.0已测试 tr只支持4.0以上(未测试)"
    # 插件图标
    plugin_icon = "https://cdn.jsdelivr.net/gh/honue/MoviePilot-Plugins@main/icon/tracker.png"
    # 插件版本
    plugin_version = "1.1"
    # 插件作者
    plugin_author = "honue"
    # 作者主页
    author_url = "https://github.com/honue"
    # 插件配置项ID前缀
    plugin_config_prefix = "trackereditor_"
    # 加载顺序
    plugin_order = 15
    # 可使用的用户级别
    auth_level = 1

    _downloader_type: str = None
    _username: str = None
    _password: str = None
    _host: str = None
    _port: int = None
    _target_domain: str = None
    _replace_domain: str = None

    _onlyonce: bool = False
    _downloader: Union[Qbittorrent, Transmission] = None

    def init_plugin(self, config: dict = None):
        if config:
            self._onlyonce = config.get("onlyonce")
            self._downloader_type = config.get("downloader_type")
            self._host = config.get("host")
            self._port = config.get("port")
            self._username = config.get("username")
            self._password = config.get("password")
            self._target_domain = config.get("target_domain")
            self._replace_domain = config.get("replace_domain")
        if self._onlyonce:
            # 执行替换
            self._task()
            self._onlyonce = False
        self.__update_config()

    def _task(self):
        if self._downloader_type == "qbittorrent":
            self._downloader = Qbittorrent(self._host, self._port, self._username, self._password)
            torrent_info_list: TorrentInfoList
            torrent_info_list, error = self._downloader.get_torrents()
            if error:
                return
            for torrent in torrent_info_list:
                for tracker in torrent.trackers:
                    if self._target_domain in tracker.url:
                        original_url = tracker.url
                        new_url = tracker.url.replace(self._target_domain, self._replace_domain)
                        logger.info(f"{original_url} 替换为\n {new_url}")
                        torrent.edit_tracker(orig_url=original_url, new_url=new_url)

        elif self._downloader_type == "transmission":
            self._downloader = Transmission(self._host, self._port, self._username, self._password)
            torrent_list: List[Torrent]
            torrent_list, error = self._downloader.get_torrents()
            if error:
                return
            for torrent in torrent_list:
                new_tracker_list = []
                for tracker in torrent.tracker_list:
                    new_url = None
                    if self._target_domain in tracker:
                        new_url = tracker.replace(self._target_domain, self._replace_domain)
                        new_tracker_list.append(new_url)
                    else:
                        new_tracker_list.append(tracker)
                        logger.info(f"{tracker} 替换为\n {new_url}")
                self._downloader.update_tracker(hash_string=torrent.hashString, tracker_list=new_tracker_list)

        logger.info("tracker替换完成")

    def __update_config(self):
        self.update_config({
            "onlyonce": self._onlyonce,
            "downloader_type": self._downloader_type,
            "username": self._username,
            "password": self._password,
            "host": self._host,
            "port": self._port,
            "target_domain": self._target_domain,
            "replace_domain": self._replace_domain
        })

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'onlyonce',
                                            'label': '立即运行一次',
                                        }
                                    }
                                ]
                            }, {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'downloader_type',
                                            'label': '下载器类型',
                                            'items': [
                                                {'title': 'Qbittorrent', 'value': 'qbittorrent'},
                                                {'title': 'Transmission', 'value': 'transmission'}
                                            ]
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'host',
                                            'label': 'host主机ip',
                                            'placeholder': '192.168.2.100'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'port',
                                            'label': 'qb/tr端口',
                                            'placeholder': '8989'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'username',
                                            'label': '用户名',
                                            'placeholder': 'username'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'password',
                                            'label': '密码',
                                            'placeholder': 'password'
                                        }
                                    }
                                ]
                            }
                        ]
                    }, {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'target_domain',
                                            'label': '待替换文本',
                                            'placeholder': 'target.com'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'replace_domain',
                                            'label': '替换的文本',
                                            'placeholder': 'replace.net'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '对下载器中所有符合代替换文本的tacker进行字符串replace替换' + '\n' +
                                                    '现有tracker: https://baidu.com/announce.php?passkey=xxxx' + '\n' +
                                                    '待替换 baidu.com 或 https://baidu.com' + '\n' +
                                                    '用于替换的文本 qq.com 或 https://qq.com' + '\n' +
                                                    '结果为 https://qq.com/announce.php?passkey=xxxx',
                                            'style': 'white-space: pre-line;'
                                        }
                                    },
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '强烈建议自己先添加一个tracker测试替换是否符合预期，程序是否正常运行',
                                            'style': 'white-space: pre-line;'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "onlyonce": False,
            "downloader_type": "qbittorrent",
            "host": "192.168.2.100",
            "port": 8989,
            "username": "username",
            "password": "password",
            "target_domain": "",
            "replace_domain": ""
        }

    def get_page(self) -> List[dict]:
        pass

    def get_state(self) -> bool:
        return self._onlyonce

    def stop_service(self):
        pass

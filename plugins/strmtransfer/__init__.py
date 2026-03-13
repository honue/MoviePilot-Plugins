from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.event import Event, eventmanager
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import TransferInfo
from app.schemas.types import EventType


class StrmTransfer(_PluginBase):
	# 插件名称
	plugin_name = "StrmTransfer"
	# 插件描述
	plugin_desc = "转移完成后，按路径前缀映射生成 STRM 文件"
	# 插件图标
	plugin_icon = "directory.png"
	# 插件版本
	plugin_version = "1.0.0"
	# 插件作者
	plugin_author = "honue"
	# 作者主页
	author_url = "https://github.com/honue"
	# 插件配置项 ID 前缀
	plugin_config_prefix = "strmtransfer_"
	# 加载顺序
	plugin_order = 1
	# 可使用的用户级别
	auth_level = 1

	_enable: bool = False
	_mp_media_prefix: str = "/downloads/link"
	_strm_prefix: str = "/strm"

	def init_plugin(self, config: dict = None):
		if config:
			self._enable = config.get("enable") or False
			self._mp_media_prefix = (config.get("mp_media_prefix") or "/downloads/link").strip()
			self._strm_prefix = (config.get("strm_prefix") or "/strm").strip()

	@eventmanager.register(EventType.TransferComplete)
	def transfer_complete(self, event: Event):
		if not self._enable:
			return

		transfer_info: Optional[TransferInfo] = event.event_data.get("transferinfo")
		if not transfer_info:
			return

		source_files = transfer_info.file_list or []
		target_files = transfer_info.file_list_new or []
		if not source_files or not target_files:
			logger.debug("转移事件缺少源或目标文件列表，跳过")
			return

		if len(source_files) != len(target_files):
			logger.warning(
				f"源/目标文件数量不一致，source={len(source_files)} target={len(target_files)}，仅处理可配对部分"
			)

		for source_file, target_file in zip(source_files, target_files):
			if not source_file or not target_file:
				continue
			self._create_strm_file(source_path=source_file, dest_path=target_file)

	def _create_strm_file(self, source_path: str, dest_path: str):
		if not self._mp_media_prefix or not self._strm_prefix:
			logger.warning("MP媒体库前缀 或 strm库前缀 未配置，跳过 STRM 生成")
			return

		# 仅目标路径以 mp_media_prefix 开头时才做前缀替换并创建 strm。
		if not dest_path.startswith(self._mp_media_prefix):
			logger.debug(f"目标路径不以 MP媒体库 前缀开头，跳过 dest={dest_path}")
			return

		strm_target = f"{self._strm_prefix}{dest_path[len(self._mp_media_prefix):]}"
		strm_path = Path(strm_target).with_suffix(".strm")

		try:
			strm_path.parent.mkdir(parents=True, exist_ok=True)
			strm_path.write_text(source_path, encoding="utf-8")
			logger.info(f"STRM 已生成：{strm_path} -> {source_path}")
		except Exception as err:
			logger.error(f"创建 STRM 失败：{strm_path}，错误：{err}")

	def get_state(self) -> bool:
		return self._enable

	@staticmethod
	def get_command() -> List[Dict[str, Any]]:
		return []

	def get_api(self) -> List[Dict[str, Any]]:
		return []

	def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
		return [
			{
				"component": "VForm",
				"content": [
					{
						"component": "VRow",
						"content": [
							{
								"component": "VCol",
								"props": {
									"cols": 12,
									"md": 4
								},
								"content": [
									{
										"component": "VSwitch",
										"props": {
											"model": "enable",
											"label": "启用插件"
										}
									}
								]
							}
						]
					},
					{
						"component": "VRow",
						"content": [
							{
								"component": "VCol",
								"props": {
									"cols": 12,
									"md": 6
								},
								"content": [
									{
										"component": "VTextField",
										"props": {
											"model": "mp_media_prefix",
											"label": "MP媒体库前缀",
											"placeholder": "/downloads/link"
										}
									}
								]
							},
							{
								"component": "VCol",
								"props": {
									"cols": 12,
									"md": 6
								},
								"content": [
									{
										"component": "VTextField",
										"props": {
											"model": "strm_prefix",
											"label": "strm库前缀",
											"placeholder": "/strm"
										}
									}
								]
							}
						]
					},
					{
						"component": "VRow",
						"content": [
							{
								"component": "VCol",
								"props": {
									"cols": 12
								},
								"content": [
									{
										"component": "VAlert",
										"props": {
											"type": "info",
											"variant": "tonal",
											"text": "监听转移完成事件：当 transfer.dest 以 MP媒体库前缀 开头时，替换为 strm库前缀 并创建同名 .strm 文件，文件内容为 transfer.src。"
										}
									}
								]
							}
						]
					},
					{
						"component": "VRow",
						"content": [
							{
								"component": "VCol",
								"props": {
									"cols": 12
								},
								"content": [
									{
										"component": "VAlert",
										"props": {
											"type": "success",
											"variant": "tonal",
											"text": "逻辑示意：TransferComplete -> 读取 transfer.src/transfer.dest -> 判断 dest.startswith(mp_media_prefix) -> 计算 strm_path(前缀替换 + 后缀改为 .strm) -> 写入内容为 transfer.src"
										}
									}
								]
							}
						]
					}
				]
			}
		], {
			"enable": self._enable,
			"mp_media_prefix": self._mp_media_prefix,
			"strm_prefix": self._strm_prefix
		}

	def get_page(self) -> Optional[List[dict]]:
		pass

	def stop_service(self):
		pass

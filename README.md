# MoviePilot-Plugins

MoviePilot非官方插件库, ANi-Strm

# MoviePilot x ANi-Strm

建议配合目录监控使用，strm文件创建在你插件填写的地址 如/downloads/strm

通过目录监控插件转移到link媒体库文件夹 如/downloads/link/strm，mp会完成刮削 这样也避免了污染正常视频文件的媒体库

```
/downloads/strm:/downloads/link/strm#move
```

<div align="center">
	<img src="./img/link.png" width="200px">
</div>

不开启一次性创建全部，则每次运行会创建ani最新季度的top15个文件。

源来自 https://aniopen.an-i.workers.dev emby需要设置代理

串流播放测试成功：
创建的Strm在串流模式下可以播放

直接播放（未知情况）：

1.在Windows小秘能播放

2.网页端和fileball播放测试失败（偶尔可以正常播放）。（emby log是tcp connect timeout）
<div align="center">
	<img src="./img/test.png">
</div>

目前先这样，接下来看看怎么解决播放的问题。

## To do:

- [ ] 网页、fileball 无法播放的问题（应该是链接timeout的问题），看看能不能解决，或者有无更好的源代替。
- [ ] 排查是否存在bug，优化使用
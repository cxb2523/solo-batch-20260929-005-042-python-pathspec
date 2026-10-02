"""
GitWildMatch/gitignore 编译链路的内部组件包。

.. warning:: 本包不是公共 API，其内容与结构随时可能变化。

编译链路拆分为四个组件：

-	:mod:`pathspec.patterns._gitwildmatch.normalize` —— 模式规范化
	（正负前缀、尾部斜杠、锚定与相对根、``**`` 片段）。
-	:mod:`pathspec.patterns._gitwildmatch.regex` —— 正则片段拼装。
-	:mod:`pathspec.patterns._gitwildmatch.compile` —— 编译编排
	（串联规范化与片段拼装，并接入缓存）。
-	:mod:`pathspec.patterns._gitwildmatch.cache` —— 线程安全的 regex 缓存。
"""
"""
regex 缓存组件：为 GitWildMatch/gitignore 编译链路提供进程内缓存。

.. warning:: 本模块不是公共 API，其内容与结构随时可能变化。

定法一：缓存键取「原始模式串」，不取「规范化中间表示」。

	键取调用方传入的原始模式串（连同解析后的 *errors* 模式），而不是
	规范化后的片段或拼装好的正则。若改用规范化中间表示作键，写法不同但
	逻辑等价的模式（如 "foo/" 与 "foo/**"、"**" 与 "**/**"）可以合并
	缓存项、提高命中率；但规范化本身要把整条流水线跑一遍，缓存就失去了
	短路意义，而且中间表示一旦随实现调整，旧键全部失效。命中率与跨平台
	一致在这里互相拉扯：原始串作键与 :mod:`re` 模块自身缓存的语义一致
	（编译什么串就缓存什么串），同一原始串在任何平台上都命中同一缓存项，
	跨平台行为可预期；规范化中间表示作键命中率略高，却让缓存语义依赖
	规范化细节，POSIX 与 Windows 风味下归一化出的中间表示可能不同，
	一致性反而更难保证。这里选择牺牲少量命中率，换取与 :mod:`re` 一致、
	跨平台稳定的缓存键语义。

定法二：批量预编译缓存不下沉进本组件。

	批量预编译（如 :meth:`GitIgnoreSpec.from_lines` 一次编译数百行）不在
	本组件内另设批量缓存。若下沉，批量缓存（键为模式集合）与单模式缓存
	（键为单条模式）就是两层缓存，失效必须双层联动：清空单模式缓存而不
	清空批量缓存会拿到过期的编译产物，反之则批量缓存整体作废、失去意义。
	批量路径逐条调用本组件的单模式缓存即可获得同样的去重收益，因此批量
	缓存留在调用方（或不设），本组件只维护单层缓存，失效语义只有一种。

线程安全：:class:`RegexCache` 用一把锁保护全部状态，编译在锁内完成，
同一模式被多个线程同时打时只编译一次；读路径用 dict 的原子 get 做
快速通道，命中时不加锁。
"""

import threading
from collections.abc import (
	Callable)
from typing import (
	Any,
	Hashable,
	Optional)


class RegexCache:
	"""
	The :class:`RegexCache` class is a thread-safe cache for compiled
	patterns. A key is compiled at most once, even when multiple threads
	request it concurrently.
	"""

	def __init__(self) -> None:
		"""
		Initializes the :class:`RegexCache` instance.
		"""
		self._lock = threading.Lock()
		self._entries: dict = {}
		self._compile_counts: dict = {}

	def get_or_compile(
		self,
		key: Hashable,
		compile_fn: Callable[[], Any],
	) -> Any:
		"""
		Get the cached entry for *key*, compiling and caching it on a miss.

		*key* is the cache key (the raw pattern string with its resolved
		error mode).

		*compile_fn* (:class:`~collections.abc.Callable`) builds the entry on
		a cache miss. It is called at most once per key, under the lock, so
		concurrent callers never trigger duplicate compiles.

		Returns the cached entry.
		"""
		# Fast path: dict get is atomic, no lock needed on a hit.
		entry = self._entries.get(key)
		if entry is None:
			with self._lock:
				# Re-check under the lock: another thread may have compiled
				# the entry while we waited.
				entry = self._entries.get(key)
				if entry is None:
					# Compile under the lock so the same pattern is compiled
					# exactly once even when hit from many threads at once.
					entry = compile_fn()
					self._entries[key] = entry
					self._compile_counts[key] = self._compile_counts.get(key, 0) + 1

		return entry

	def compile_count(self, key: Hashable) -> int:
		"""
		Get how many times *key* was actually compiled (0 if never).

		*key* is the cache key.

		Returns the compile count (:class:`int`).
		"""
		return self._compile_counts.get(key, 0)

	def clear(self) -> None:
		"""
		Drop all cached entries and compile counts.
		"""
		with self._lock:
			self._entries.clear()
			self._compile_counts.clear()

	def __len__(self) -> int:
		"""
		Returns the number of cached entries (:class:`int`).
		"""
		return len(self._entries)
"""
This module provides the regex cache component of the gitignore (gitwildmatch)
compile chain. It guarantees that the same regular expression is compiled at
most once process-wide, even when multiple threads race on the same pattern.

DECISION (cache key): the cache is keyed on the translated regular expression
string -- the exact argument handed to :func:`re.compile` -- not on the raw
gitignore pattern, and not on the normalized intermediate representation.
Keying on the raw pattern string would fragment the cache ("foo/", "foo/**"
and "/foo/**" all compile to the same regex but would each get their own
entry), dragging down the hit rate. Keying on the normalized intermediate
(the segment tuple) would raise the hit rate, but it couples the cache to the
normalization rules: any platform-dependent divergence in normalization would
silently change which entries are shared, pulling cross-platform consistency
down with it. The regex string is the platform-independent final
representation, so it maximizes the hit rate and keeps Windows and POSIX
behavior identical. It also preserves the existing cache-key semantics:
:func:`re.compile` itself has always been called with this string, and its
own internal cache is keyed on it, so observable behavior is unchanged.

DECISION (no batch precompile cache): the batch precompile cache is
deliberately NOT sunk into this component. :meth:`GitIgnoreSpec.from_lines`
compiles its patterns one at a time through this cache, so a batch-level
cache would be a second layer keyed on whole pattern collections. Two layers
would both need invalidation: clearing the per-pattern cache would leave
stale compiled regexes reachable through the batch layer, and clearing the
batch layer would not free per-pattern entries -- a double-layer invalidation
problem with no owner. Keeping a single layer means :meth:`RegexCache.clear`
is the only invalidation point and :attr:`RegexCache.compile_count` stays an
accurate count of actual compilations.
"""

import re
import threading
from typing import (
	Dict)  # Replaced by `dict` in 3.9.

from pathspec._typing import (
	AnyStr)  # Removed in 3.18.


class RegexCache(object):
	"""
	The :class:`RegexCache` class is a thread-safe cache of compiled regular
	expressions. The same regex is compiled at most once: threads racing on
	the same key wait on the lock and reuse the first compilation.
	"""

	# Keep the class dict-less.
	__slots__ = (
		'_cache',
		'_compile_count',
		'_lock',
	)

	def __init__(self) -> None:
		"""
		Initializes the :class:`RegexCache` instance.
		"""
		self._cache: Dict[AnyStr, re.Pattern] = {}
		self._compile_count = 0
		self._lock = threading.Lock()

	@property
	def compile_count(self) -> int:
		"""
		The number of actual compilations performed (:class:`int`). Cache hits
		do not increment this.
		"""
		return self._compile_count

	def __len__(self) -> int:
		"""
		Returns the number of cached compiled regexes (:class:`int`).
		"""
		return len(self._cache)

	def clear(self) -> None:
		"""
		Drop all cached regexes and reset the compile count. This is the only
		invalidation point (see the module docstring for why there is no
		second, batch-level cache to keep in sync).
		"""
		with self._lock:
			self._cache.clear()
			self._compile_count = 0

	def get_or_compile(self, raw_regex: AnyStr) -> re.Pattern:
		"""
		Get the compiled regex for *raw_regex*, compiling it on first use.

		*raw_regex* (:class:`str` or :class:`bytes`) is the uncompiled regular
		expression (the cache key; see the module docstring).

		Returns the compiled regex (:class:`re.Pattern`).
		"""
		try:
			# Fast path: cache hit. Dict reads are atomic under the GIL, and
			# entries are never mutated or evicted individually, so no lock is
			# needed here.
			return self._cache[raw_regex]
		except KeyError:
			pass

		with self._lock:
			# Re-check under the lock: another thread may have compiled the
			# same regex while we waited. This is what guarantees the same
			# pattern is compiled only once even under a thread race.
			regex = self._cache.get(raw_regex)
			if regex is None:
				regex = re.compile(raw_regex)
				self._cache[raw_regex] = regex
				self._compile_count += 1

			return regex


REGEX_CACHE = RegexCache()
"""
The process-wide regex cache (:class:`RegexCache`) used by the gitignore
compile chain.
"""

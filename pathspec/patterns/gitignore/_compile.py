"""
This module provides the compilation and matching component of the gitignore
(gitwildmatch) compile chain. Compilation is routed through
:data:`pathspec.patterns.gitignore._cache.REGEX_CACHE` so the same regex is
compiled at most once process-wide, and matching is a thin forward to the
compiled regex: the pattern classes keep no matching logic of their own.
"""

import re
from collections.abc import (
	Iterable,
	Iterator)
from typing import (
	Optional)  # Replaced by `X | None` in 3.10.

from pathspec._typing import (
	AnyStr)  # Removed in 3.18.
from pathspec.pattern import (
	RegexMatchResult)

from ._cache import (
	REGEX_CACHE)


def compile_regex(raw_regex: AnyStr) -> re.Pattern:
	"""
	Compile the regular expression through the process-wide regex cache.

	*raw_regex* (:class:`str` or :class:`bytes`) is the uncompiled regular
	expression produced by the fragment assembly component.

	Returns the compiled regex (:class:`re.Pattern`).
	"""
	return REGEX_CACHE.get_or_compile(raw_regex)


def match_file(
	regex: Optional[re.Pattern],
	file: AnyStr,
) -> Optional[RegexMatchResult]:
	"""
	Match the compiled regex against the specified file. This is the
	forwarding target for :meth:`GitIgnoreSpecPattern.match_file`.

	*regex* (:class:`re.Pattern` or :data:`None`) is the compiled regular
	expression, or :data:`None` for a null-operation pattern.

	*file* (:class:`str` or :class:`bytes`) is the file path relative to the
	root directory (e.g., "relative/path/to/file").

	Returns the match result (:class:`.RegexMatchResult`) if *file* matched;
	otherwise, :data:`None`.
	"""
	if (
		regex is not None
		and (match := regex.search(file)) is not None
	):
		return RegexMatchResult(match)

	return None


def match_files(
	regex: Optional[re.Pattern],
	files: Iterable[AnyStr],
) -> Iterator[AnyStr]:
	"""
	Match the compiled regex against each of the specified files. This is the
	forwarding target for :meth:`GitIgnoreSpecPattern.match_files`.

	*regex* (:class:`re.Pattern` or :data:`None`) is the compiled regular
	expression, or :data:`None` for a null-operation pattern.

	*files* (:class:`~collections.abc.Iterable` of :class:`str`) contains each
	file relative to the root directory.

	Returns an :class:`~collections.abc.Iterator` yielding each matched file
	path (:class:`str`).
	"""
	for file in files:
		if match_file(regex, file) is not None:
			yield file

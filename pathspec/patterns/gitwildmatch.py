"""
.. version-deprecated: 1.0.0
	This module is superseded by :module:`pathspec.patterns.gitignore`.
"""

from collections.abc import (
	Iterable,
	Iterator)
from typing import (
	Optional)  # Replaced by `X | None` in 3.10.

from pathspec import util
from pathspec.pattern import (
	RegexMatchResult)
from pathspec._typing import (
	AnyStr,  # Removed in 3.18.
	deprecated,  # Added in 3.13.
	override)  # Added in 3.12.

from ._gitwildmatch.compile import (
	match_file as _compiled_match_file)
from .gitignore.spec import (
	GitIgnoreSpecPattern)

# DEPRECATED: Deprecated since version 1.0.0. Expose GitWildMatchPatternError
# in this module for backward compatibility.
from .gitignore import (
	GitIgnorePatternError as GitWildMatchPatternError)


class GitWildMatchPattern(GitIgnoreSpecPattern):
	"""
	.. version-deprecated:: 1.0.0
		This class is superseded by :class:`GitIgnoreSpecPattern` and
		:class:`~pathspec.patterns.gitignore.basic.GitIgnoreBasicPattern`.
	"""

	@deprecated((
		"GitWildMatchPattern ('gitwildmatch') is deprecated. Use 'gitignore' for "
		"GitIgnoreBasicPattern or GitIgnoreSpecPattern instead."
	))
	def __init__(self, *args, **kw) -> None:
		"""
		Warn about deprecation.
		"""
		super().__init__(*args, **kw)

	@override
	@classmethod
	@deprecated((
		"GitWildMatchPattern ('gitwildmatch') is deprecated. Use 'gitignore' for "
		"GitIgnoreBasicPattern or GitIgnoreSpecPattern instead."
	))
	def pattern_to_regex(cls, *args, **kw):
		"""
		Warn about deprecation.
		"""
		return super().pattern_to_regex(*args, **kw)

	@override
	def match_file(self, file: AnyStr) -> Optional[RegexMatchResult]:
		"""
		Matches this pattern against the specified file.

		This method only forwards to the compiled regular expression produced
		by the compile pipeline components
		(:mod:`pathspec.patterns._gitwildmatch`); it contains no matching
		logic of its own.

		*file* (:class:`str` or :class:`bytes`) is the file path relative to
		the root directory.

		Returns the match result (:class:`.RegexMatchResult`) if *file*
		matched; otherwise, :data:`None`.
		"""
		return _compiled_match_file(self.regex, file)

	def match_files(self, files: Iterable[AnyStr]) -> Iterator[AnyStr]:
		"""
		Matches this pattern against the specified files.

		This method only forwards: it loops over *files* and delegates each
		one to :meth:`match_file`.

		*files* (:class:`~collections.abc.Iterable` of :class:`str`) contains
		each file relative to the root directory.

		Returns an :class:`~collections.abc.Iterator` yielding each matched
		file path (:class:`str`).
		"""
		for file in files:
			if self.match_file(file) is not None:
				yield file


# DEPRECATED: Deprecated since version 1.0.0. Register GitWildMatchPattern as
# "gitwildmatch" for backward compatibility.
util.register_pattern('gitwildmatch', GitWildMatchPattern)
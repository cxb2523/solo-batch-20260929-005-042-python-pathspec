"""
编译组件：GitWildMatch/gitignore 编译链路的编排层。

.. warning:: 本模块不是公共 API，其内容与结构随时可能变化。

本组件把整条编译链路串起来：

	规范化（:mod:`.normalize`） -> 片段拼装（:mod:`.regex`） ->
	编译（:func:`re.compile`） -> 缓存（:mod:`.cache`）

-	:func:`translate_pattern` 只做规范化与拼装，返回未编译的正则，
	是 ``GitIgnoreSpecPattern.pattern_to_regex`` 的实现。
-	:func:`compile_pattern` 走共享缓存，返回 :class:`CompileEntry`
	（含编译好的正则），是 ``GitIgnoreSpecPattern.__init__`` 的实现。
-	:func:`match_file` 是匹配转发点，``GitWildMatchPattern.match_file``
	只转发到这里，自身不含任何匹配逻辑。

批量预编译缓存不下沉进本组件的原因见 :mod:`.cache` 的模块说明。
"""

import re
from dataclasses import (
	dataclass)
from typing import (
	Literal,
	Optional,  # Replaced by `X | None` in 3.10.
	Union)  # Replaced by `X | Y` in 3.10.

from ...pattern import (
	RegexMatchResult)
from ..._typing import (
	AnyStr,  # Removed in 3.18.
	assert_unreachable)
from ..gitignore.base import (
	GitIgnorePatternError,
	_BYTES_ENCODING,
	_PosixClassError,
	_RangeNotationError,
	_TrailingBackslashError)

from .cache import (
	RegexCache)
from .normalize import (
	NormalizedPattern,
	normalize_pattern)
from .regex import (
	translate_segments)

_REGEX_CACHE = RegexCache()
"""
The process-wide cache shared by all gitwildmatch/gitignore-spec pattern
compilations. Keyed by the raw pattern string and its resolved error mode.
"""


@dataclass()
class CompileEntry(object):
	"""
	The :class:`CompileEntry` data class is the cached result of compiling a
	pattern: the uncompiled regex, the include flag, the compiled regular
	expression, and the normalized intermediate form.
	"""

	# Keep the class dict-less.
	__slots__ = (
		'compiled',
		'include',
		'normalized',
		'pattern',
		'regex',
	)

	pattern: Union[str, bytes]
	"""
	*pattern* (:class:`str` or :class:`bytes`) is the raw pattern as passed in.
	"""

	regex: Optional[Union[str, bytes]]
	"""
	*regex* (:class:`str`, :class:`bytes`, or :data:`None`) is the uncompiled
	regular expression.
	"""

	include: Optional[bool]
	"""
	*include* (:class:`bool` or :data:`None`) is whether matched files should
	be included (:data:`True`), excluded (:data:`False`), or is a
	null-operation (:data:`None`).
	"""

	compiled: Optional['re.Pattern']
	"""
	*compiled* (:class:`re.Pattern` or :data:`None`) is the compiled regular
	expression.
	"""

	normalized: Optional[NormalizedPattern]
	"""
	*normalized* (:class:`.NormalizedPattern` or :data:`None`) is the
	normalized intermediate form, or :data:`None` for a null-operation.
	"""


def _decode_pattern(pattern: Union[AnyStr, 're.Pattern', None]) -> tuple:
	"""
	Decode *pattern* to a string.

	Returns a :class:`tuple` of the pattern string (:class:`str`) and the
	return type (:class:`type`).
	"""
	if isinstance(pattern, str):
		return (pattern, str)
	elif isinstance(pattern, bytes):
		return (pattern.decode(_BYTES_ENCODING), bytes)
	else:
		raise TypeError(f"{pattern=!r} is not a unicode or byte string.")


def _resolve_errors(
	errors: Optional[Literal['literal', 'null', 'raise']],
) -> tuple:
	"""
	Resolve the *errors* mode.

	Returns a :class:`tuple` of the resolved *errors* mode and the segment
	translation error mode.
	"""
	if errors is None:
		errors = 'null'
	elif errors not in ('literal', 'null', 'raise'):
		raise ValueError(f"{errors=!r} is not a valid value.")

	seg_errors: Literal['literal', 'raise']
	if errors == 'null':
		seg_errors = 'raise'
	elif errors in ('literal', 'raise'):
		seg_errors = errors
	else:
		assert_unreachable(f"Failed to map {errors=!r} to seg_errors.")

	return (errors, seg_errors)


def _assemble_regex(
	normalized: NormalizedPattern,
	seg_errors: Literal['literal', 'raise'],
	errors: str,
) -> Optional[str]:
	"""
	Assemble the regular expression from the normalized pattern.

	Returns the regular expression (:class:`str`), or :data:`None` when the
	pattern is a null-operation under *errors*.
	"""
	if normalized.regex_override is not None:
		# Use regex override.
		return normalized.regex_override

	assert normalized.segments is not None, (
		f"{normalized.regex_override=} and {normalized.segments=} cannot both be null."
	)

	# Build regular expression from pattern.
	try:
		regex_parts = translate_segments(
			seg_errors, normalized.is_dir_pattern, normalized.segments,
		)
	except (_PosixClassError, _RangeNotationError) as e:
		if errors == 'raise':
			raise GitIgnorePatternError((
				f"Invalid git pattern: {normalized.original!r}"
			)) from e  # GitIgnorePatternError
		else:
			return None
	except _TrailingBackslashError as e:
		# EDGE CASE: The gitignore docs say a trailing backlash is invalid and
		# never matches.
		if errors == 'raise':
			raise GitIgnorePatternError((
				f"Invalid git pattern: {normalized.original!r}"
			)) from e  # GitIgnorePatternError
		else:
			return None

	return ''.join(regex_parts)


def translate_pattern(
	pattern: AnyStr,
	*,
	errors: Optional[Literal['literal', 'null', 'raise']] = None,
) -> tuple:
	"""
	Convert the pattern into an uncompiled regular expression. This is the
	implementation of ``GitIgnoreSpecPattern.pattern_to_regex``.

	*pattern* (:class:`str` or :class:`bytes`) is the pattern to convert into
	a regular expression.

	*errors* (:class:`str` or :data:`None`) is how to handle invalid notation
	in the pattern. Default is :data:`None` for ``'null'``.

	Raises :exc:`.GitIgnorePatternError` if the pattern fails to process,
	regardless of the value of *errors*.

	Returns a :class:`tuple` containing the uncompiled regular expression
	(:class:`str`, :class:`bytes`, or :data:`None`) and the include flag
	(:class:`bool` or :data:`None`).
	"""
	pattern_str, return_type = _decode_pattern(pattern)
	errors, seg_errors = _resolve_errors(errors)

	# Normalize the pattern (prefixes, trailing slash, anchoring, '**').
	normalized = normalize_pattern(pattern_str)
	if normalized is None:
		# Null-operation (comment, blank, or a single '/').
		return (None, None)

	regex = _assemble_regex(normalized, seg_errors, errors)
	if regex is None:
		return (None, None)

	# Encode regex if needed.
	out_regex: AnyStr
	if return_type is bytes:
		regex_bytes = regex.encode(_BYTES_ENCODING)
		out_regex = regex_bytes  # type: ignore[assignment]
	else:
		out_regex = regex  # type: ignore[assignment]

	return (out_regex, normalized.include)


def _compile_uncached(
	pattern: Union[str, bytes],
	errors: str,
	seg_errors: str,
) -> CompileEntry:
	"""
	Compile *pattern* without consulting the cache. This runs at most once
	per cache key (enforced by :class:`.RegexCache`).
	"""
	pattern_str, return_type = _decode_pattern(pattern)

	normalized = normalize_pattern(pattern_str)
	if normalized is None:
		# Null-operation (comment, blank, or a single '/').
		return CompileEntry(
			pattern=pattern,
			regex=None,
			include=None,
			compiled=None,
			normalized=None,
		)

	regex = _assemble_regex(normalized, seg_errors, errors)
	if regex is None:
		return CompileEntry(
			pattern=pattern,
			regex=None,
			include=None,
			compiled=None,
			normalized=normalized,
		)

	# Encode regex if needed.
	out_regex: Union[str, bytes]
	if return_type is bytes:
		out_regex = regex.encode(_BYTES_ENCODING)
	else:
		out_regex = regex

	return CompileEntry(
		pattern=pattern,
		regex=out_regex,
		include=normalized.include,
		compiled=re.compile(out_regex),
		normalized=normalized,
	)


def compile_pattern(
	pattern: Union[str, bytes],
	*,
	errors: Optional[Literal['literal', 'null', 'raise']] = None,
) -> CompileEntry:
	"""
	Compile the pattern, using the shared cache so the same raw pattern is
	compiled at most once in this process.

	*pattern* (:class:`str` or :class:`bytes`) is the pattern to compile.

	*errors* (:class:`str` or :data:`None`) is how to handle invalid notation
	in the pattern. Default is :data:`None` for ``'null'``.

	Raises :exc:`.GitIgnorePatternError` if the pattern fails to process,
	regardless of the value of *errors*.

	Returns the :class:`CompileEntry`.
	"""
	# Resolve (and validate) the error mode before the cache lookup so an
	# invalid mode always raises, even on a would-be cache hit.
	resolved_errors, seg_errors = _resolve_errors(errors)

	# The cache key is the raw pattern string with its resolved error mode.
	# See the cache module docstring for why the key is the raw pattern and
	# not the normalized intermediate representation.
	key = (resolved_errors, pattern)
	return _REGEX_CACHE.get_or_compile(key, lambda: _compile_uncached(
		pattern, resolved_errors, seg_errors,
	))


def compile_count(
	pattern: Union[str, bytes],
	errors: Optional[Literal['literal', 'null', 'raise']] = None,
) -> int:
	"""
	Get how many times *pattern* was actually compiled in the shared cache.
	Used by ``dev/serve.py`` to report compile counts.

	Returns the compile count (:class:`int`).
	"""
	resolved_errors, _seg_errors = _resolve_errors(errors)
	return _REGEX_CACHE.compile_count((resolved_errors, pattern))


def match_file(
	regex: Optional['re.Pattern'],
	file: AnyStr,
) -> Optional[RegexMatchResult]:
	"""
	Match forwarding point: ``GitWildMatchPattern.match_file`` forwards here
	and contains no matching logic of its own.

	*regex* (:class:`re.Pattern` or :data:`None`) is the compiled regular
	expression produced by :func:`compile_pattern`.

	*file* (:class:`str` or :class:`bytes`) is the file path relative to the
	root directory.

	Returns the match result (:class:`.RegexMatchResult`) if *file* matched;
	otherwise, :data:`None`.
	"""
	if (
		regex is not None
		and (match := regex.search(file)) is not None
	):
		return RegexMatchResult(match)

	return None
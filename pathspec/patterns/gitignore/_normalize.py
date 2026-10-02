"""
This module provides the pattern normalization component of the gitignore
(gitwildmatch) compile chain. It takes the raw pattern string and produces a
:class:`NormalizedPattern`: the include/exclude flag from the ``!`` prefix,
the directory flag from the trailing slash, the anchor flag and root-relative
segments, and the collapsed ``**`` segments (or a regex override for the
degenerate match-all patterns).

DECISION (anchoring): anchor detection normalizes first and only then judges,
instead of dispatching to :func:`posixpath.isabs` / :func:`ntpath.isabs` per
platform. Git defines gitignore semantics platform-independently: the
separator is always ``/`` and a backslash is an escape character, never a
separator. Splitting the decision by ``os.path`` flavor would make behavior
diverge on Windows -- since Python 3.13, ``ntpath.isabs("/foo")`` is
:data:`False` (a single leading slash is drive-relative there), so the same
pattern would be root-anchored on POSIX but floating on Windows. Normalizing
to the POSIX form first keeps the anchored result identical on both
platforms, which is what git does.
"""

from dataclasses import (
	dataclass)
from typing import (
	Optional)  # Replaced by `X | None` in 3.10.

from .base import (
	_strip_trailing_ws)
from ._fragments import (
	_DIR_MARK_CG,
	_MATCH_ALL)


@dataclass()
class NormalizedPattern(object):
	"""
	The :class:`NormalizedPattern` data class is the output of the
	normalization component: everything the fragment assembly component needs
	to build the regular expression, without re-inspecting the raw pattern.
	"""

	# Keep the class dict-less.
	__slots__ = (
		'anchored',
		'include',
		'is_dir_pattern',
		'regex_override',
		'segments',
	)

	include: Optional[bool]
	"""
	*include* (:class:`bool` or :data:`None`) is whether matched files should
	be included (:data:`True`), excluded (:data:`False`), or the pattern is a
	null-operation (:data:`None`; comment, blank, or a single ``/``).
	"""

	is_dir_pattern: bool
	"""
	*is_dir_pattern* (:class:`bool`) is whether the pattern is a directory
	pattern (i.e., ends with a slash ``/``).
	"""

	anchored: bool
	"""
	*anchored* (:class:`bool`) is whether the pattern is anchored to the root
	directory (a slash at the beginning or in the middle of the pattern). A
	trailing slash only marks a directory pattern and does not anchor.
	"""

	segments: Optional[list[str]]
	"""
	*segments* (:class:`list` of :class:`str` or :data:`None`) is the
	normalized, root-relative pattern segments, or :data:`None` when the
	pattern is a null-operation or has a regular expression override.
	"""

	regex_override: Optional[str]
	"""
	*regex_override* (:class:`str` or :data:`None`) is the regular expression
	to use instead of assembling one from :attr:`segments` (the degenerate
	match-all patterns such as ``**`` and ``*``).
	"""


def is_root_anchored(path: str) -> bool:
	"""
	Check whether *path* is anchored to the (filesystem or repository) root.

	This implements the normalize-first decision described in the module
	docstring: separators are normalized to ``/`` and the result is judged on
	the normalized form, so a Windows root (``C:/foo``, ``C:\\foo``) and a
	POSIX root (``/foo``) are both anchored, regardless of the platform this
	code runs on. Dispatching to :func:`posixpath.isabs` on POSIX and
	:func:`ntpath.isabs` on Windows instead would make ``/foo`` unanchored on
	Windows (Python 3.13+), breaking cross-platform consistency.

	*path* (:class:`str`) is the path or pattern to check.

	Returns whether *path* is anchored (:class:`bool`).
	"""
	norm = path.replace('\\', '/')
	if norm.startswith('/'):
		# POSIX root (or a gitignore pattern with a leading slash).
		return True

	# Windows drive root (e.g., "C:/..."), rooted once normalized.
	return len(norm) >= 3 and norm[0].isalpha() and norm[1] == ':' and norm[2] == '/'


def normalize_segments(
	is_dir_pattern: bool,
	pattern_segs: list[str],
) -> tuple[Optional[list[str]], Optional[str]]:
	"""
	Normalize the pattern segments to make processing easier.

	*is_dir_pattern* (:class:`bool`) is whether the pattern is a directory
	pattern (i.e., ends with a slash '/').

	*pattern_segs* (:class:`list` of :class:`str`) contains the pattern
	segments. This may be modified in place.

	Raises :exc:`ValueError` if the pattern normalizes to nothing.

	Returns a :class:`tuple` containing either:

	- The normalized segments (:class:`list` of :class:`str`; or :data:`None`).

	- The regular expression override (:class:`str` or :data:`None`).
	"""
	if not pattern_segs[0]:
		# A pattern beginning with a slash ('/') should match relative to the root
		# directory. Remove the empty first segment to make the pattern relative
		# to root.
		del pattern_segs[0]

	elif len(pattern_segs) == 1 or (len(pattern_segs) == 2 and not pattern_segs[1]):
		# A single segment pattern with or without a trailing slash ('/') will
		# match any descendant path. This is equivalent to "**/{pattern}". Prepend
		# a double-asterisk segment to make the pattern relative to root.
		if pattern_segs[0] != '**':
			pattern_segs.insert(0, '**')

	else:
		# A pattern without a beginning slash ('/') but contains at least one
		# prepended directory (e.g., "dir/{pattern}") should match relative to
		# the root directory. No segment modification is needed.
		pass

	if not pattern_segs:
		# After normalization, we end up with no pattern at all. This must be
		# because the pattern is invalid.
		raise ValueError("Pattern normalized to nothing.")

	if not pattern_segs[-1]:
		# A pattern ending with a slash ('/') will match all descendant paths if
		# it is a directory but not if it is a regular file. This is equivalent to
		# "{pattern}/**". Set the empty last segment to a double-asterisk to
		# include all descendants.
		pattern_segs[-1] = '**'

	# EDGE CASE: Collapse duplicate double-asterisk sequences (i.e., '**/**').
	# Iterate over the segments in reverse order and remove the duplicate double
	# asterisks as we go.
	for i in range(len(pattern_segs) - 1, 0, -1):
		prev = pattern_segs[i-1]
		seg = pattern_segs[i]
		if prev == '**' and seg == '**':
			del pattern_segs[i]

	seg_count = len(pattern_segs)
	if seg_count == 1 and pattern_segs[0] == '**':
		if is_dir_pattern:
			# The pattern "**/" will be normalized to "**", but it should match
			# everything except for files in the root. Special case this pattern.
			return (None, _DIR_MARK_CG)
		else:
			# The pattern "**" will match every path. Special case this pattern.
			return (None, _MATCH_ALL)

	elif (
		seg_count == 2
		and pattern_segs[0] == '**'
		and pattern_segs[1] == '*'
	):
		# The pattern "*" will be normalized to "**/*" and will match every
		# path. Special case this pattern for efficiency.
		return (None, _MATCH_ALL)

	elif (
		seg_count == 3
		and pattern_segs[0] == '**'
		and pattern_segs[1] == '*'
		and pattern_segs[2] == '**'
	):
		# The pattern "*/" will be normalized to "**/*/**" which will match every
		# file not in the root directory. Special case this pattern for
		# efficiency.
		if is_dir_pattern:
			return (None, _DIR_MARK_CG)
		else:
			return (None, '/')

	# No regular expression override, return modified pattern segments.
	return (pattern_segs, None)


def normalize_pattern(pattern_str: str) -> NormalizedPattern:
	"""
	Normalize the raw gitignore pattern string.

	This is the first stage of the compile chain. It handles, in order:
	trailing whitespace stripping (escaped whitespace is kept), comment
	(``#``) and single-``/`` null-operations, the negation prefix (``!``),
	blank null-operations, the trailing-slash directory flag, the anchor flag
	(see the module docstring for why this is judged on the normalized form),
	and the root-relative ``**`` segment normalization.

	*pattern_str* (:class:`str`) is the raw pattern (already decoded to
	:class:`str` if it was :class:`bytes`).

	Raises :exc:`ValueError` if the pattern normalizes to nothing.

	Returns the normalized pattern (:class:`NormalizedPattern`).
	"""
	# Strip trailing whitespace.
	pattern_str = _strip_trailing_ws(pattern_str)

	include: Optional[bool]

	if pattern_str.startswith('#'):
		# A pattern starting with a hash ('#') serves as a comment (neither
		# includes nor excludes files). Escape the hash with a backslash to match
		# a literal hash (i.e., '\#').
		return NormalizedPattern(None, False, False, None, None)

	elif pattern_str == '/':
		# EDGE CASE: According to `git check-ignore` (v2.4.1), a single '/' does
		# not match any file.
		return NormalizedPattern(None, False, False, None, None)

	if pattern_str.startswith('!'):
		# A pattern starting with an exclamation mark ('!') negates the pattern
		# (exclude instead of include). Escape the exclamation mark with a
		# backslash to match a literal exclamation mark (i.e., '\!').
		include = False
		# Remove leading exclamation mark.
		pattern_str = pattern_str[1:]
	else:
		include = True

	if not pattern_str:
		# A blank pattern is a null-operation (neither includes nor excludes
		# files).
		return NormalizedPattern(None, False, False, None, None)

	# Split pattern into segments.
	orig_segs = pattern_str.split('/')

	# Check whether the pattern is specifically a directory pattern before
	# normalization.
	is_dir_pattern = not orig_segs[-1]

	# A slash at the beginning or in the middle of the pattern anchors it to
	# the root directory. A trailing slash only marks a directory pattern and
	# does not anchor.
	if is_dir_pattern:
		anchored = '/' in pattern_str[:-1]
	else:
		anchored = '/' in pattern_str

	# Normalize the segments (root-relative, collapsed '**').
	segments, regex_override = normalize_segments(is_dir_pattern, orig_segs)

	return NormalizedPattern(include, is_dir_pattern, anchored, segments, regex_override)

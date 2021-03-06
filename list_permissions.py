#!/usr/bin/python3

# This program basically implements
# find /.backup_etc/etc -print0 | xargs -0 stat --printf="chmod %a %n\n"
# But with the option of printing the paths relative. Doing this in bash would require awk/sed/nonsense

import argparse
import os
import sys
import stat
import pwd
import grp
import io
import re
import logging

import check_ignore

# This path quoting stuff is from shlex.py in the Python 3 stdlib,
# modified to work with bytes-like objects.
# Unix paths are, on one hand, arbitrary bytes, and this script
# treats them as so.
_find_unsafe = re.compile(rb'[^\w@%+=:,./-]', re.ASCII).search

def quote(s, always_quote=False):
	"""Return a shell-escaped version of the string *s*."""
	if not always_quote:
		if not s:
			return b"''"
		if _find_unsafe(s) is None:
			return s

	# use single quotes, and put single quotes into double quotes
	# the string $'b is then quoted as '$'"'"'b'
	return b"'" + s.replace(b"'", b"'\"'\"'") + b"'"


def uquote(s, always_quote=False):
	"""Return a shell-escaped version of the string *s*."""
	if not always_quote:
		if not s:
			return "''"
		if _find_unsafe(s) is None:
			return s

	# use single quotes, and put single quotes into double quotes
	# the string $'b is then quoted as '$'"'"'b'
	return "'" + s.replace("'", "'\"'\"'") + "'"


def chmod_line(name_path, name_stat):
	chmod_permissions_bits = stat.S_IMODE(name_stat.st_mode)
	# etckeeper has four digits in its chmod output, we shall too.
	chmod_permission_bytestring = "{:04o}".format(chmod_permissions_bits).encode()

	line = b"".join([
		b"maybe chmod ",
		chmod_permission_bytestring, b" ",
		quote(name_path, always_quote=True),
		b"\n"])
	return line


def chown_line(name_path, name_stat):
	group_id = name_stat[stat.ST_GID]
	user_id = name_stat[stat.ST_UID]
	group_name = grp.getgrgid(group_id).gr_name
	user_name = pwd.getpwuid(user_id).pw_name
	# linux user/group names should only be ascii chars and therefore python strings
	comment = "# {}:{}".format(user_id, group_id)
	line = b"".join([
		"maybe chown {}:{} ".format(user_name, group_name).encode(),
		b" ", quote(name_path, always_quote=True),
		b" ", comment.encode(), b"\n"])
	return line

def chown_line_etckeeper_style(name_path, name_stat):
	user_id = name_stat[stat.ST_UID]
	user_name = pwd.getpwuid(user_id).pw_name
	# linux user/group names should only be ascii chars and therefore python strings
	line = b"".join([
		"maybe chown {}".format(uquote(user_name, always_quote=True)).encode(),
		b" ", quote(name_path, always_quote=True), b"\n"])
	return line

def chgrp_line_etckeeper_style(name_path, name_stat):
	group_id = name_stat[stat.ST_GID]
	group_name = grp.getgrgid(group_id).gr_name
	# linux user/group names should only be ascii chars and therefore python strings
	line = b"".join([
		"maybe chgrp {}".format(uquote(group_name, always_quote=True)).encode(),
		b" ", quote(name_path, always_quote=True), b"\n"])
	return line


def make_safe_rel_path(path, root):
	"""all arguments and return are bytes type."""
	rel_path = os.path.normpath(os.path.relpath(path, root_path))
	# prevents filenames with leading "-" from being interpreted as an argument
	safe_rel_path = os.path.join(b"./", rel_path) # assumes POSIX paths
	return safe_rel_path


def do_it(root_path, logger, all_mentioned_paths=None):
	"""
	root_path should be a bytes object representing a path on the system

	returns a [(key, line)...]. The script should just be the lines, but perhaps arranged by key.
	"""

	CHMOD_RANKING = 1
	MKDIR_RANKING = 0

	commands = []

	def make_chmod_command(name_path, name_stat):
		return ((CHMOD_RANKING, name_path, 0), chmod_line(name_path, name_stat))

	def make_mkdir_command(dir_path):
		line = b"".join([
			b"mkdir -p ",
			quote(dir_path, always_quote=True),
			b"\n"])

		return ((MKDIR_RANKING, dir_path, 0), line)

	def make_chown_command(name_path, name_stat):
		return ((CHMOD_RANKING, name_path, 1), chown_line(name_path, name_stat))

	def make_etckeeper_chown_command(name_path, name_stat):
		return ((CHMOD_RANKING, name_path, -2), chown_line_etckeeper_style(name_path, name_stat))

	def make_etckeeper_chgrp_command(name_path, name_stat):
		return ((CHMOD_RANKING, name_path, -1), chgrp_line_etckeeper_style(name_path, name_stat))

	#it's not easy to write this as an anon. function because Python lambdas accept expressions and "raise" is a statement.
	def raiser(x):
		raise x

	# Walk through directory. Halt on error.
	# etckeeper includes permissions of the root ".". This might be bad when root is /
	# but for now repeat the behavior
	commands.append(make_chmod_command(b".", os.stat(root_path, follow_symlinks=False)))

	last_reported_dir_path = None # for debugging

	# os.walk does not follow symlinks by default. Good because otherwise it could loop.
	for dir_path, dir_names, file_names in os.walk(root_path, topdown=True, onerror=raiser):
		if not dir_path == last_reported_dir_path:
			last_reported_dir_path == dir_path
			logger.debug("recursing into: {}".format(dir_path.decode("utf-8", "replace")))
		# by deleting names from `dir_names`, os.walk will not recurse down ignored paths.
		for _names in [dir_names, file_names]:
			for _name in list(_names): # make a copy, since we're mutating list
				# if we knew there were no .git directories not corresponding with the repo whose
				# ignore rules we want, then we could just have git_root_path == root_path
				# and we would not need the absolute path stuff.
				# however, that is not the case.

				git_root_path = b"/"

				# there should be a less clumbsy way to get the relpath.
				# Probably there's no need to make an abspath to begin with.
				# This is to avoid git bugs related to absolute pathspecs
				path_to_check_abs = os.path.abspath(os.path.join(dir_path, _name))
				path_to_check = os.path.relpath(path_to_check_abs, git_root_path)
				ignore = check_ignore.is_path_git_ignored(
						git_root_path,
						path_to_check)

				if ignore:
					logger.debug("git says ignore: {}".format(path_to_check.decode("utf-8", "replace")))
					_names.remove(_name)

		names = dir_names + file_names

		if len(names) == 0:
			# dir_path is an empty dir
			safe_rel_dir_path = make_safe_rel_path(dir_path, root_path)
			commands.append(make_mkdir_command(safe_rel_dir_path))

		for name in names:
			name_path = os.path.join(dir_path, name)
			safe_rel_name_path = make_safe_rel_path(name_path, root_path)

			name_stat = os.stat(name_path, follow_symlinks=False)

			if True: # compatible with etc
				# etckeeper only lists permissions for regular files and directories.
				# on linux, symlinks do not have file permissions, only their targets.
				# apparently on macOS, symlinks do have their own permissions.
				if (stat.S_ISREG(name_stat.st_mode) or stat.S_ISDIR(name_stat.st_mode)):
					if all_mentioned_paths is not None:
						#rel path in the way that git ls-files returns
						all_mentioned_paths.append(os.path.relpath(name_path, b"/"))

					commands.append(make_chmod_command(safe_rel_name_path, name_stat))
					# etckeeper only lists users/groups if they're not equal to this script's uid/gid
					if not name_stat[stat.ST_UID] == os.getuid():
						commands.append(make_etckeeper_chown_command(safe_rel_name_path, name_stat))
					if not name_stat[stat.ST_GID] == os.getgid():
						commands.append(make_etckeeper_chgrp_command(safe_rel_name_path, name_stat))

			else:
				commands.append(make_chmod_command(name_path, name_stat))
				commands.append(make_chown_command(name_path, name_stat))

	return commands

if __name__=="__main__":
	ap = argparse.ArgumentParser(
		description="List the permissions of files and folder in a directory",
		)
	ap.add_argument("path", type=str)
	ap.add_argument("--log", help="log level (INFO, DEBUG, ...)")
	args = ap.parse_args()

	if args.log:
		# assuming loglevel is bound to the string value obtained from the
		# command line argument. Convert to upper case to allow the user to
		# specify --log=DEBUG or --log=debug
		numeric_level = getattr(logging, args.log.upper(), None)
		if not isinstance(numeric_level, int):
			raise ValueError('Invalid log level: %s' % args.log)
		logging.basicConfig(file=sys.stderr, level=numeric_level)


	# paths in UNIX are not encoded strings. They are bytes.
	# Python decodes the bytes in args,
	root_path = os.fsencode(args.path) # https://bugs.python.org/issue8776 and PEP383

	sys.stdout.buffer.write(b"# Generated by list_permissions.py.  Do not edit.\n")

	sys.stdout.buffer.write(b" ".join([b"cd", quote(os.path.abspath(root_path)), b"\n"]))

	mentioned_paths = []
	commands = do_it(root_path, logging, mentioned_paths)

	ls_files = check_ignore.git_ls_files(root_path)

	# We don't want to mention symlinks in the .etckeeper because symlinks on
	# linux do not carry permission metadata
	ls_files_no_symlinks = [ relpath for relpath in ls_files if not
		stat.S_ISLNK(os.stat(os.path.join(root_path, relpath), follow_symlinks=False).st_mode)
		]

	files_missed = set(ls_files_no_symlinks) - set(mentioned_paths)

	if len(files_missed) > 0:
		raise RuntimeError("Files reported by git ls-files but not included: {}".format(
			"\n".join(map(lambda x: x.decode("utf-8", "replace"), sorted(files_missed)))))

	sys.stdout.buffer.write(b"".join([line for (rank, line) in sorted(commands)]))

	sys.exit()

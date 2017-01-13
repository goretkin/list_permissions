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

# This path quoting stuff is from shlex.py in the Python 3 stdlib,
# modified to work with bytes-like objects.
# Unix paths are, on one hand, arbitrary bytes, and this script
# treats them as so.
_find_unsafe = re.compile(rb'[^\w@%+=:,./-]', re.ASCII).search

def quote(s, always_quote=False):
	"""Return a shell-escaped version of the string *s*."""
	if not always_quote:
		if not s:
			return "''"
		if _find_unsafe(s) is None:
			return s

	# use single quotes, and put single quotes into double quotes
	# the string $'b is then quoted as '$'"'"'b'
	return b"'" + s.replace(b"'", b"'\"'\"'") + b"'"


def chmod_line(name_path):
	name_stat = os.stat(name_path, follow_symlinks=False)

	chmod_permissions_bits = stat.S_IMODE(name_stat.st_mode)
	# etckeeper has four digits in its chmod output, we shall too.
	chmod_permission_bytestring = "{:04o}".format(chmod_permissions_bits).encode()

	line = b"".join([
		b"maybe chmod ",
		chmod_permission_bytestring, b" ",
		quote(name_path, always_quote=True),
		b"\n"])
	return line


def chown_line(name_path):
	name_stat = os.stat(name_path, follow_symlinks=False)
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


def do_it(root_path):
	"""
	root_path should be a bytes object representing a path on the system
	
	returns a [(key, line)...]. The script should just be the lines, but perhaps arranged by key.
	"""

	CHMOD_RANKING = 1
	MKDIR_RANKING = 0

	commands = []

	def make_chmod_command(name_path):
		return ((CHMOD_RANKING, name_path, 0), chmod_line(name_path))

	def make_mkdir_command(dir_path):
		line = b"".join([
			b"mkdir -p ",
			quote(dir_path, always_quote=True),
			b"\n"])

		return ((MKDIR_RANKING, dir_path, 0), line)

	def make_chown_command(name_path):
		return ((CHMOD_RANKING, name_path, 1), chown_line(name_path))

	#it's not easy to write this as an anon. function because Python lambdas accept expressions and "raise" is a statement.
	def raiser(x):
		raise x

	# Walk through directory. Halt on error.
	# etckeeper includes permissions of the root ".". This might be bad when root is /
	# but for now repeat the behavior
	commands.append(make_chmod_command(root_path))

	for dir_path, dir_names, file_names in os.walk(root_path, topdown=True, onerror=raiser):
		names = dir_names + file_names

		if len(names) == 0:
			# dir_path is an empty dir
			commands.append(make_mkdir_command(dir_path))

		for name in names:
			name_path = os.path.join(dir_path, name)
			commands.append(make_chmod_command(name_path))
			commands.append(make_chown_command(name_path))

	return commands

if __name__=="__main__":
	ap = argparse.ArgumentParser(
		description="List the permissions of files and folder in a directory",
		)
	ap.add_argument("path", type=str)
	args = ap.parse_args()

	# paths in UNIX are not encoded strings. They are bytes.
	# Python decodes the bytes in args, 
	root_path = os.fsencode(args.path) # https://bugs.python.org/issue8776 and PEP383

	sys.stdout.buffer.write(b" ".join([b"cd", quote(os.path.abspath(root_path)), b"\n"]))

	commands = do_it(root_path)

	sys.stdout.buffer.write(b"".join([line for (rank, line) in sorted(commands)]))

	sys.exit()


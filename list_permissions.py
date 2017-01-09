#!/usr/bin/python3

# This program basically implements
# find /.backup_etc/etc -print0 | xargs -0 stat --printf="chmod %a %n\n"
# But with the option of printing the paths relative. Doing this in bash would require awk/sed/nonsense

import argparse
import os
import sys
import stat
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

def do_it(root_path, mkdir_output_stream, chmod_output_stream):
	"""
	root_path should be a bytes object representing a path on the system
	"""
	
	#it's not easy to write this as an anon. function because Python lambdas accept expressions and "raise" is a statement.
	def raiser(x):
		raise x
	
	# Walk through directory. Halt on error.
	# etckeeper includes permissions of the root ".". This might be bad when root is /
	# but for now repeat the behavior
	chmod_output_stream.write(chmod_line(root_path))
	for dir_path, dir_names, file_names in os.walk(root_path, topdown=True, onerror=raiser):
		dir_names.sort() # descend in a sorted order. os.walk is okay with this.
		
		names = dir_names + file_names

		if len(names) == 0:
			# dir_path is an empty dir
			line = b"".join([
				b"mkdir -p ",
				quote(dir_path, always_quote=True),
				b"\n"])
			mkdir_output_stream.write(line)

		for name in sorted(names):
			name_path = os.path.join(dir_path, name)

			chmod_output_stream.write(chmod_line(name_path))


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
	
	chmod_buffer = io.BytesIO()
	do_it(root_path, sys.stdout.buffer, chmod_buffer)
	sys.stdout.buffer.write(chmod_buffer.getvalue())
	chmod_buffer.close()

	sys.exit()
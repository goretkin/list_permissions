import subprocess
import os

def is_path_git_ignored(git_root, path):
	"""
	`path` is relative to `git_root`
	"""
	if os.path.isabs(path):
		raise Warning("Prior to git-1.9.4, absolute paths are buggy.")
	# this works even if path contains e.g. \n
	o = subprocess.Popen(["git", "-C", git_root, "check-ignore", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
	stdout, stderr = o.communicate() # close the process
	if o.returncode == 0:
		return True
	if o.returncode == 1:
		return False
	
	raise RuntimeError("git returned {}. stdout: {} stderr: {}".format(o.returncode, stdout, stderr))

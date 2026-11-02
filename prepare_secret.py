"""Create a standalone secret projection without printing it or replacing a key."""
import argparse
import os
from pathlib import Path
import secrets
import stat

def prepare(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o777 != 0o700:
        raise ValueError("Secret directory must be private (0700) and owned by this user")
    path = directory / "session-key"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)
    except FileExistsError:
        if path.is_symlink() or not path.is_file() or len(path.read_text().strip()) < 32:
            raise ValueError("Invalid existing key; refusing replacement")
    else:
        os.fchmod(descriptor, 0o444)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(secrets.token_urlsafe(48))
            stream.flush()
            os.fsync(stream.fileno())
    return path

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory")
    args = parser.parse_args()
    prepare(args.directory)
    print("Private Session-Schlüsseldatei bereit; kein Schlüsselwert ausgegeben.")

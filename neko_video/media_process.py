"""Internal exec wrapper to bound Linux ffprobe/ffmpeg resources, not a sandbox."""

import os
import sys


def main():
    if sys.platform.startswith("linux"):
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
        resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    os.execv(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()

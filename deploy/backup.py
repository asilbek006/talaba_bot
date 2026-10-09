#!/usr/bin/env python3
"""Create a database dump plus durable files; never put credentials in argv."""

import argparse
import datetime
import os
import shutil
import subprocess
import tarfile
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


def main():
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=Path, default=Path("/opt/talaba_bot/.env"))
    parser.add_argument("--files", type=Path, default=Path("/var/lib/talaba_bot"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    load_dotenv(args.env)
    parsed = urlparse(os.environ["DATABASE_URL"])
    if parsed.scheme not in ("postgres", "postgresql") or not parsed.path.strip("/"):
        raise ValueError("DATABASE_URL must name a PostgreSQL database")
    if not args.files.is_dir():
        raise ValueError("Files directory does not exist")
    if args.output.resolve().is_relative_to(args.files.resolve()):
        raise ValueError("Backup output must be outside the files directory")
    env = os.environ.copy()
    env.update(
        PGHOST=parsed.hostname or "localhost",
        PGPORT=str(parsed.port or 5432),
        PGUSER=unquote(parsed.username or ""),
        PGPASSWORD=unquote(parsed.password or ""),
        PGDATABASE=unquote(parsed.path.lstrip("/")),
    )
    parameters = parse_qs(parsed.query)
    for key in ("sslmode", "sslrootcert", "sslcert", "sslkey", "connect_timeout"):
        if key in parameters:
            env["PG" + key.upper()] = parameters[key][-1]
    destination = args.output / datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )
    destination.mkdir(parents=True, mode=0o700)
    try:
        subprocess.run(
            ["pg_dump", "--format=custom", "--file", str(destination / "database.dump")],
            env=env,
            check=True,
            timeout=600,
        )
        with tarfile.open(destination / "files.tar.gz", "w:gz") as archive:
            archive.add(
                args.files,
                arcname="files",
                filter=lambda item: None if "/.tmp" in item.name else item,
            )
        for path in destination.iterdir():
            path.chmod(0o600)
        print(destination)
    except BaseException:
        shutil.rmtree(destination)
        raise


if __name__ == "__main__":
    main()

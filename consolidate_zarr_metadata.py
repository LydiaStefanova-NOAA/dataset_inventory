#!/usr/bin/env python3
import argparse
import os
import sys
import fsspec
import zarr


def storage_opts(uri: str, auth: bool) -> dict:
    # default anonymous unless --auth
    anon = not auth
    if uri.startswith("gs://"):
        return {} if auth else {"token": "anon"}
    if uri.startswith("s3://"):
        return {} if auth else {"anon": True}
    return {}


def open_store(uri: str, auth: bool):
    if uri.startswith(("s3://", "gs://", "http://", "https://")):
        mapper = fsspec.get_mapper(uri, **storage_opts(uri, auth))
        return mapper
    return uri  # local path


def main():
    p = argparse.ArgumentParser(description="Consolidate Zarr metadata in-place.")
    p.add_argument("target", help="Path/URI to zarr store (local, s3://, gs://)")
    p.add_argument("--auth", action="store_true", help="Use cloud credentials (not anonymous)")
    p.add_argument("--dry-run", action="store_true", help="Check access only, do not modify")
    args = p.parse_args()

    target = os.path.expanduser(args.target)

    if not (target.endswith(".zarr") or os.path.isdir(target) or target.startswith(("s3://", "gs://", "http://", "https://"))):
        print(f"Error: target does not look like a zarr store: {target}")
        sys.exit(2)

    store = open_store(target, args.auth)

    # Validate readable
    try:
        z = zarr.open_group(store=store, mode="r")
        print(f"Readable Zarr store: {target}")
        print(f"Top-level arrays/groups: {len(list(z.group_keys()))} groups, {len(list(z.array_keys()))} arrays")
    except Exception as e:
        print(f"Error: cannot read zarr store: {e}")
        sys.exit(1)

    if args.dry_run:
        print("Dry run: not writing metadata.")
        return

    # Write consolidated metadata
    try:
        zarr.consolidate_metadata(store)
        print("Success: wrote consolidated metadata (.zmetadata / equivalent).")
    except Exception as e:
        print(f"Error: failed to consolidate metadata (need write permission?): {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
import argparse
import os
import re
import sys
import warnings

# Suppress only known noisy backend warnings (avoid hiding real failures)
warnings.filterwarnings("ignore", module="cfgrib")
warnings.filterwarnings("ignore", module="xarray.backends")

import fsspec
import numpy as np
import xarray as xr

SHORTCUTS = {
    "era5": "gs://gcp-public-data-arco-era5/ar/1959-2022-1h-360x181_equiangular_with_poles_conservative.zarr",
    "era5_local": "/scratch3/NCEPDEV/global/Lydia.B.Stefanova/project/SFSbeta/data/era5_monthly_1deg_1991-2022.zarr",
    "sfs": "s3://noaa-oar-sfsdev-pds/experiments/beta1/reforecast/05/atm_monthly.zarr",
    "sfs_atm": "s3://noaa-oar-sfsdev-pds/experiments/beta1/reforecast/05/atm_monthly.zarr",
    "sfs_ice": "s3://noaa-oar-sfsdev-pds/experiments/beta1/reforecast/05/ice_monthly.zarr",
    "sfs_ocn": "s3://noaa-oar-sfsdev-pds/experiments/beta1/reforecast/05/ocn_monthly.zarr",
    "oras5": "https://arco.datastores.ecmwf.int/cadl-arco-geo-001/arco/reanalysis_oras5/consolidated/geoChunked.zarr",
    "oras5_consolidated": "https://arco.datastores.ecmwf.int/cadl-arco-geo-001/arco/reanalysis_oras5/consolidated/geoChunked.zarr",
    "oras5_operational": "https://arco.datastores.ecmwf.int/cadl-arco-geo-001/arco/reanalysis_oras5/operational/geoChunked.zarr",
}

DATASET_EXTENSIONS = (".zarr", ".nc", ".grib", ".grib2", ".grb", ".grb2", ".h5", ".hdf5", ".hdf")
GRIB_EXTENSIONS = (".grib", ".grib2", ".grb", ".grb2")
NETCDF_EXTENSIONS = (".nc", ".nc4", ".cdf")
HDF_EXTENSIONS = (".h5", ".hdf5", ".hdf")

# Dimensions to exclude from vertical level listings
EXCLUDED_LEVEL_DIMS = {
    "latitude", "longitude", "lat", "lon", "x", "y",
    "rlat", "rlon", "time", "valid_time"
}


def get_cds_api_key() -> str | None:
    """Detect CDS API Key from env or ~/.cdsapirc."""
    env_key = os.getenv("CDSAPI_KEY")
    if env_key:
        return env_key.strip().strip('"\'')
    cdsapirc_path = os.path.expanduser("~/.cdsapirc")
    if not os.path.exists(cdsapirc_path):
        return None
    try:
        with open(cdsapirc_path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s.startswith("key:"):
                    return s.split(":", 1)[1].strip().strip('"\'')
    except Exception:
        pass
    return None


def sanitize_target(target: str) -> str:
    """Convert common web console links into canonical gs:// or s3:// URIs."""
    gcp_match = re.match(
        r"https?://console\.cloud\.google\.com/storage/browser/(?:_details/)?([^/?#;]+)(?:/([^?#;]*))?",
        target,
    )
    if gcp_match:
        bucket, path = gcp_match.groups()
        path = path.rstrip("/") if path else ""
        return f"gs://{bucket}/{path}" if path else f"gs://{bucket}"

    gcs_http_match = re.match(
        r"https?://storage\.googleapis\.com/([^/?#;]+)(?:/([^?#;]*))?",
        target,
    )
    if gcs_http_match:
        bucket, path = gcs_http_match.groups()
        path = path.rstrip("/") if path else ""
        return f"gs://{bucket}/{path}" if path else f"gs://{bucket}"

    web_match = re.match(
        r"https?://([^.]+)\.s3\.amazonaws\.com/index\.html#(.*)", target
    )
    if web_match:
        bucket, key = web_match.groups()
        return f"s3://{bucket}/{key}"

    http_s3_match = re.match(
        r"https?://([^.]+)\.s3\.amazonaws\.com/(.*)", target
    )
    if http_s3_match:
        bucket, key = http_s3_match.groups()
        return f"s3://{bucket}/{key}"

    return target


def _is_local_zarr(path: str) -> bool:
    return os.path.isdir(path) and any(
        os.path.exists(os.path.join(path, marker))
        for marker in (".zmetadata", ".zgroup", ".zarray", "zarr.json")
    )


def _storage_options_for_uri(uri: str, anon: bool) -> dict:
    """Return storage options; default to anonymous for public cloud data."""
    if uri.startswith("gs://"):
        return {"token": "anon"} if anon else {}
    if uri.startswith("s3://"):
        return {"anon": True} if anon else {}
    return {}


def _to_local_if_remote(path: str, anon: bool) -> str:
    """Cache remote object to local file for engines requiring local seekable paths (e.g., cfgrib, HTTP NetCDF)."""
    if path.startswith(("s3://", "gs://", "http://", "https://")):
        cache_url = f"simplecache::{path}"
        cache_kwargs = {"simplecache": {"same_names": True}}
        if path.startswith("s3://"):
            cache_kwargs["s3"] = _storage_options_for_uri(path, anon)
        elif path.startswith("gs://"):
            cache_kwargs["gs"] = _storage_options_for_uri(path, anon)
        elif path.startswith(("http://", "https://")):
            headers = {}
            if "datastores.ecmwf.int" in path.lower():
                cds_key = get_cds_api_key()
                if cds_key:
                    headers["Authorization"] = f"Bearer {cds_key}"
            if headers:
                cache_kwargs["http"] = {"headers": headers}
                cache_kwargs["https"] = {"headers": headers}
        return fsspec.open_local(cache_url, **cache_kwargs)
    return path


def sniff_local_magic(path: str) -> str | None:
    """
    Return quick format hint from first bytes:
      - 'grib'   if starts with b'GRIB'
      - 'netcdf' if starts with b'CDF'
      - 'hdf5'   if HDF5 signature
    """
    try:
        with open(path, "rb") as f:
            head = f.read(16)
        if head.startswith(b"GRIB"):
            return "grib"
        if head.startswith(b"CDF"):
            return "netcdf"
        if head.startswith(b"\x89HDF\r\n\x1a\n"):
            return "hdf5"
    except Exception:
        return None
    return None


def _open_grib(path: str, anon: bool) -> list[xr.Dataset]:
    import cfgrib
    local_path = _to_local_if_remote(path, anon)
    return cfgrib.open_datasets(local_path)


def open_any_datasets(target: str, anon: bool = True) -> list[xr.Dataset]:
    """
    Open Zarr, NetCDF/HDF, or GRIB (local or remote) and return list of xarray Datasets.
    Includes guarded GRIB fallback probe for files without standard suffixes.
    """
    expanded_target = os.path.expanduser(target)
    lower_target = expanded_target.lower()
    is_remote = expanded_target.startswith(("s3://", "gs://", "http://", "https://"))
    base_lower = os.path.basename(lower_target)

    # Fail fast for missing local paths
    if not is_remote and not os.path.exists(expanded_target):
        raise FileNotFoundError(f"Local path does not exist: {expanded_target}")

    # Check for Zarr format prior to rejecting directory targets
    is_zarr = lower_target.endswith(".zarr") or _is_local_zarr(expanded_target)

    # Never run dataset engines on local directories UNLESS it is a valid Zarr store
    if os.path.isdir(expanded_target) and not is_zarr:
        raise IsADirectoryError(f"Target is a directory: {expanded_target}")

    is_grib_ext = lower_target.endswith(GRIB_EXTENSIONS)
    local_magic = sniff_local_magic(expanded_target) if os.path.isfile(expanded_target) else None

    # 1) Zarr
    if is_zarr:
        if expanded_target.startswith(("gs://", "s3://")):
            return [xr.open_zarr(expanded_target, storage_options=_storage_options_for_uri(expanded_target, anon))]
        if "datastores.ecmwf.int" in lower_target:
            cds_key = get_cds_api_key()
            if not cds_key:
                print("Warning: ECMWF URL detected but no CDS key found in $CDSAPI_KEY or ~/.cdsapirc.")
            storage_opts = {"headers": {"Authorization": f"Bearer {cds_key}"}} if cds_key else {}
            return [xr.open_zarr(expanded_target, storage_options=storage_opts)]
        if expanded_target.startswith(("http://", "https://")):
            try:
                return [xr.open_zarr(expanded_target)]
            except Exception:
                mapper = fsspec.get_mapper(expanded_target)
                return [xr.open_zarr(mapper)]
        return [xr.open_zarr(expanded_target)]

    # 2) GRIB by extension
    if is_grib_ext:
        if expanded_target.startswith(("s3://", "gs://", "http://", "https://")):
            print("Caching remote GRIB file locally for ecCodes engine...")
        return _open_grib(expanded_target, anon)

    # 3) Local magic-byte sniffing (for missing/odd extensions)
    if os.path.isfile(expanded_target):
        if local_magic == "grib":
            print("Detected GRIB via magic bytes.")
            return _open_grib(expanded_target, anon)
        if local_magic == "netcdf":
            print("Detected NetCDF classic via magic bytes.")
            return [xr.open_dataset(expanded_target)]
        if local_magic == "hdf5":
            print("Detected HDF5/NetCDF4 via magic bytes.")
            return [xr.open_dataset(expanded_target)]

    # 4) Non-zarr remote files (NetCDF/HDF/etc.)
    if expanded_target.startswith(("s3://", "gs://")):
        fs_opts = _storage_options_for_uri(expanded_target, anon)
        fs, clean_path = fsspec.core.url_to_fs(expanded_target, **fs_opts)
        with fs.open(clean_path, "rb") as f:
            try:
                return [xr.open_dataset(f)]
            except Exception:
                pass  # fall through to guarded GRIB fallback

    elif expanded_target.startswith(("http://", "https://")):
        try:
            local_path = _to_local_if_remote(expanded_target, anon)
            return [xr.open_dataset(local_path)]
        except Exception:
            pass  # fall through to guarded GRIB fallback

    else:
        # 5) Local/other files: xarray autodetect first
        try:
            return [xr.open_dataset(expanded_target)]
        except Exception:
            pass  # fall through to guarded GRIB fallback

    # 6) Guarded GRIB fallback: only if there's a GRIB hint.
    grib_hint = (
        is_grib_ext
        or (local_magic == "grib")
        or ("grib" in base_lower)
        or ("grb" in base_lower)
    )

    if not grib_hint:
        raise ValueError(f"Unsupported or unrecognized dataset format: {target}")

    try:
        grib_sets = _open_grib(expanded_target, anon)
        if grib_sets:
            print("Detected GRIB content via fallback probe.")
            return grib_sets
    except Exception:
        pass

    raise ValueError(f"Could not open dataset: {target}")


def format_time_info(ds: xr.Dataset) -> str | None:
    """Report both initial and valid times when present; otherwise common time coords."""
    def summarize(coord_name: str) -> str | None:
        if coord_name not in ds.coords:
            return None
        coord = ds.coords[coord_name]
        if coord.size == 0:
            return None
        try:
            vals = np.atleast_1d(coord.values)
            start_val = str(vals[0])[:19]
            end_val = str(vals[-1])[:19]
            count = len(vals)
            if count == 1:
                return f"{coord_name}: {start_val} (1 timestep)"
            return f"{coord_name}: {start_val} to {end_val} ({count} timesteps)"
        except Exception:
            return None

    parts = []
    for name in ("initial_time", "forecast_reference_time", "valid_time", "time"):
        s = summarize(name)
        if s:
            parts.append(s)

    if not parts:
        return None

    deduped = []
    seen = set()
    for p in parts:
        if p not in seen:
            deduped.append(p)
            seen.add(p)

    return " | ".join(deduped)


def format_level_values(coord_da: xr.DataArray, max_items: int = 40) -> str:
    try:
        vals = np.atleast_1d(coord_da.values)
    except Exception:
        return ""

    formatted = []
    for v in vals:
        if isinstance(v, (int, np.integer)):
            formatted.append(str(v))
        elif isinstance(v, (float, np.floating)):
            formatted.append(str(int(v)) if float(v).is_integer() else f"{v:.4g}")
        else:
            formatted.append(str(v))

    if len(formatted) <= max_items:
        return ", ".join(formatted)

    half = max_items // 2
    return f"{', '.join(formatted[:half])}, ..., {', '.join(formatted[-half:])}"


def format_variable_info(var_name: str, var: xr.DataArray, ds: xr.Dataset, show_levels: bool = False) -> str:
    """Format variable name, dimensions, metadata, and optional level values."""
    dims_str = f"({', '.join(f'{d}: {var.sizes[d]}' for d in var.dims)})" if var.dims else "()"

    long_name = var.attrs.get("long_name") or var.attrs.get("description") or ""
    units = var.attrs.get("units") or ""

    details = []
    if long_name:
        details.append(str(long_name))
    if units:
        details.append(f"[{units}]")

    # GRIB qualifiers to distinguish inst/avg/accum and step ranges
    step_type = var.attrs.get("GRIB_stepType", var.attrs.get("stepType"))
    step_range = var.attrs.get("GRIB_stepRange", var.attrs.get("stepRange"))
    stat_proc = var.attrs.get("GRIB_typeOfStatisticalProcessing", var.attrs.get("typeOfStatisticalProcessing"))
    if step_type or step_range or stat_proc is not None:
        grib_parts = []
        if step_type:
            grib_parts.append(f"stepType={step_type}")
        if step_range:
            grib_parts.append(f"stepRange={step_range}")
        if stat_proc is not None and str(stat_proc) != "":
            grib_parts.append(f"statProc={stat_proc}")
        if grib_parts:
            details.append(f"({', '.join(grib_parts)})")

    details_str = f" : {' '.join(details)}" if details else ""

    main_line = f" - {var_name} {dims_str}{details_str}"
    if not show_levels:
        return main_line

    level_lines = []
    for d in var.dims:
        if d.lower() not in EXCLUDED_LEVEL_DIMS and d in ds.coords:
            vals_str = format_level_values(ds.coords[d])
            if vals_str:
                level_lines.append(f"    └ {d} levels: {vals_str}")

    return main_line + ("\n" + "\n".join(level_lines) if level_lines else "")


def print_dataset_summary(datasets: list[xr.Dataset]) -> None:
    total_vars = sum(len(ds.data_vars) for ds in datasets)
    print(f"Datasets opened: {len(datasets)}")
    print(f"Total data variables (raw): {total_vars}")

    for idx, ds in enumerate(datasets, 1):
        prefix = f"Group {idx}: " if len(datasets) > 1 else ""
        dims_desc = ", ".join(f"{k}={v}" for k, v in ds.sizes.items()) if ds.sizes else "(none)"
        coords_desc = ", ".join(ds.coords.keys()) if ds.coords else "(none)"
        print(f"{prefix}dims[{dims_desc}]  coords[{coords_desc}]  vars={len(ds.data_vars)}")
        t = format_time_info(ds)
        if t:
            print(f"  {t}")


def classify_directory_entries(target: str, anon: bool, limit: int = 15) -> None:
    """
    Smarter directory classification for local/cloud paths.
    Categories: zarr stores, netcdf/hdf files, grib-like files, other.
    """
    expanded = os.path.expanduser(target)

    if os.path.isdir(expanded):
        names = os.listdir(expanded)
        fulls = [os.path.join(expanded, n) for n in names]

        def is_local_zarr_dir(p: str) -> bool:
            return os.path.isdir(p) and any(
                os.path.exists(os.path.join(p, marker))
                for marker in (".zmetadata", ".zgroup", ".zarray", "zarr.json")
            )

        zarrs, netcdf_hdf, gribs, other = [], [], [], []
        for name, full in zip(names, fulls):
            low = name.lower()
            if is_local_zarr_dir(full) or low.endswith(".zarr"):
                zarrs.append(name)
            elif low.endswith(NETCDF_EXTENSIONS) or low.endswith(HDF_EXTENSIONS):
                netcdf_hdf.append(name)
            elif low.endswith(GRIB_EXTENSIONS) or ("grib" in low) or ("grb" in low):
                gribs.append(name)
            else:
                other.append(name)

    else:
        opts = _storage_options_for_uri(target, anon) if target.startswith(("s3://", "gs://")) else {}
        fs, clean_path = fsspec.core.url_to_fs(target, **opts)
        items = fs.ls(clean_path, detail=True)

        zarrs, netcdf_hdf, gribs, other = [], [], [], []
        for it in items:
            name = it["name"] if isinstance(it, dict) else str(it)
            base = name.rstrip("/").split("/")[-1]
            low = base.lower()
            is_dir = isinstance(it, dict) and it.get("type") == "directory"

            if low.endswith(".zarr") or (is_dir and low.endswith(".zarr")):
                zarrs.append(base)
            elif low.endswith(NETCDF_EXTENSIONS) or low.endswith(HDF_EXTENSIONS):
                netcdf_hdf.append(base)
            elif low.endswith(GRIB_EXTENSIONS) or ("grib" in low) or ("grb" in low):
                gribs.append(base)
            else:
                other.append(base)

    print("Directory classification:")
    print(f" - zarr stores: {len(zarrs)}")
    print(f" - netcdf/hdf files: {len(netcdf_hdf)}")
    print(f" - grib-like files: {len(gribs)}")
    print(f" - other: {len(other)}")

    def show(label: str, arr: list[str]):
        if not arr:
            return
        print(f"\n{label} (showing up to {limit}):")
        for x in arr[:limit]:
            print(f" - {x}")
        if len(arr) > limit:
            print(f" ... and {len(arr) - limit} more.")

    show("Zarr", zarrs)
    show("NetCDF/HDF", netcdf_hdf)
    show("GRIB-like", gribs)
    show("Other", other)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Inventory variables in Zarr, NetCDF/HDF, or GRIB datasets (local or remote)."
    )
    p.add_argument("input", help="FILE_OR_URL_OR_SHORTCUT")
    p.add_argument("output_file", nargs="?", default=None, help="Optional output text file")
    p.add_argument("--levels", "-l", action="store_true", help="Show coordinate level values for non-horizontal dims")
    p.add_argument("--summary", action="store_true", help="Print compact dataset summary only")
    p.add_argument("--auth", action="store_true", help="Use cloud credentials instead of anonymous access")
    return p.parse_args()


def main():
    args = parse_args()
    raw_input = args.input
    output_file = args.output_file
    show_levels = args.levels
    summary_only = args.summary
    anon = not args.auth  # default anonymous, opt-in to credentials with --auth

    target = SHORTCUTS.get(raw_input, raw_input)
    target = sanitize_target(target)

    if target != raw_input:
        print(f"Cleaned target URI: {target}\n")

    datasets = []
    try:
        datasets = open_any_datasets(target, anon=anon)

        if summary_only:
            print_dataset_summary(datasets)
            return

        time_lines = []
        for idx, ds in enumerate(datasets, 1):
            time_summary = format_time_info(ds)
            if time_summary:
                prefix = f"Group {idx} " if len(datasets) > 1 else ""
                time_lines.append(f"[{prefix}{time_summary}]")
        if time_lines:
            print("\n".join(time_lines))
            print()

        formatted_vars = []
        seen_keys = set()

        for ds in datasets:
            for name, var in ds.data_vars.items():
                grib_sig = (
                    str(var.attrs.get("GRIB_stepType", var.attrs.get("stepType", ""))),
                    str(var.attrs.get("GRIB_stepRange", var.attrs.get("stepRange", ""))),
                    str(var.attrs.get("GRIB_typeOfStatisticalProcessing", var.attrs.get("typeOfStatisticalProcessing", ""))),
                    str(var.attrs.get("GRIB_forecastTime", var.attrs.get("forecastTime", ""))),
                )

                key = (
                    name,
                    tuple(var.dims),
                    tuple(var.sizes[d] for d in var.dims),
                    str(var.attrs.get("long_name") or var.attrs.get("description") or ""),
                    str(var.attrs.get("units") or ""),
                    grib_sig,
                )
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                formatted_vars.append(format_variable_info(name, var, ds, show_levels=show_levels))

        print(f"Found {len(formatted_vars)} unique variables across {len(datasets)} level group(s):\n")

        if output_file:
            with open(output_file, "w", encoding="utf-8") as f:
                f.write("\n".join(formatted_vars) + "\n")
            print(f"Saved variable metadata to '{output_file}'")
        else:
            for line in formatted_vars:
                print(line)
        return

    except Exception as e:
        expanded = os.path.expanduser(target)

        if isinstance(e, FileNotFoundError):
            print(f"Error: local path not found: {expanded}")
            sys.exit(1)

        # If this looks like a directory/prefix, provide smarter classification
        if os.path.isdir(expanded) or target.endswith("/") or target.startswith(("s3://", "gs://")):
            try:
                classify_directory_entries(target, anon=anon)
                return
            except Exception:
                pass

        if expanded.lower().endswith(DATASET_EXTENSIONS) or os.path.exists(expanded):
            print(f"Error reading dataset '{raw_input}': {e}")
            sys.exit(1)

        print(f"Error reading target '{raw_input}': {e}")
        sys.exit(1)

    finally:
        for ds in datasets:
            try:
                ds.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()

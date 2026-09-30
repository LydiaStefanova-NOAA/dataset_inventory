# `inventory.py` — Dataset Inventory Tool

`inventory.py` inventories variables and metadata from common geoscience dataset formats across local paths and cloud/object storage.

It currently supports:

- **Zarr** (`.zarr`, including cloud/object stores)
- **NetCDF / NetCDF4 / HDF5** (`.nc`, `.nc4`, `.cdf`, `.h5`, `.hdf5`, `.hdf`)
- **GRIB/GRIB2** (`.grib`, `.grib2`, `.grb`, `.grb2`)
- **Directory classification mode** for local or cloud prefixes

---

## Quick start

```bash
python inventory.py <input>
```

Examples:

```bash
python inventory.py era5
python inventory.py /path/to/file.nc
python inventory.py s3://bucket/path/data.zarr
python inventory.py gs://bucket/path/file.grib2
```

---

## CLI usage

```bash
python inventory.py INPUT [OUTPUT_FILE] [--levels] [--summary] [--auth]
```

### Positional arguments

- `INPUT`  
  File path, directory, URL/URI, or shortcut key (see [Shortcuts](#shortcuts)).

- `OUTPUT_FILE` (optional)  
  Write variable inventory output to a text file instead of stdout.

### Options

- `--levels`, `-l`  
  Include non-horizontal coordinate level values per variable.

- `--summary`  
  Print compact dataset/group summary only (dims, coords, variable counts, time coverage).

- `--auth`  
  Use cloud credentials (instead of default anonymous access) for `s3://` and `gs://`.

---

## Shortcuts

Built-in aliases currently include:

- `era5`
- `era5_local`
- `sfs`, `sfs_atm`, `sfs_ice`, `sfs_ocn`
- `oras5`, `oras5_consolidated`, `oras5_operational`

Example:

```bash
python inventory.py sfs --summary
```

---

## Behavior notes

## 1) Anonymous by default for cloud buckets

For `s3://` and `gs://`, the tool defaults to anonymous reads.

- Use **no** `--auth` for public data.
- Use `--auth` only when authenticated access is required. (NOT TESTED)

## 2) GRIB time reporting

When available, time output includes multiple relevant coordinates, such as:

- `initial_time`
- `forecast_reference_time`
- `valid_time`
- `time`

## 3) GRIB differentiation for “same variable name” cases

To avoid collapsing statistically different GRIB fields (e.g., instantaneous vs average), inventory uniqueness includes GRIB signatures like:

- `stepType`
- `stepRange`
- `typeOfStatisticalProcessing`
- `forecastTime`

## 4) Directory inputs

If `INPUT` is a directory (local or cloud-like prefix path), the tool prints a classification summary:

- zarr stores
- netcdf/hdf files
- grib-like files
- other

This is intentional and avoids treating directories as files.

## 5) Nonexistent local paths

Missing local paths fail fast with a clean message (no backend stack trace spam).

## 6) Unrecognized file formats

If input is a real file but not recognized as supported format, the tool exits cleanly with an “unsupported/unrecognized dataset format” style error.

---

## Examples

## Standard inventory

```bash
python inventory.py /data/sample.grib2
```

## Include level values

```bash
python inventory.py /data/sample.nc --levels
```

## Summary-only mode

```bash
python inventory.py /data/sample.zarr --summary
```

## Save output to file

```bash
python inventory.py /data/sample.nc inventory.txt
```

## Classify a directory

```bash
python inventory.py /scratch/my_project/
```

## Cloud path (public bucket)

```bash
python inventory.py s3://my-public-bucket/path/file.nc
```

## Cloud path (authenticated/private)

```bash
python inventory.py s3://my-private-bucket/path/file.nc --auth
```

---

## Typical output

## Variable inventory mode

- Time coverage line(s), if detected
- Count of unique variables across opened groups
- Per-variable line with:
  - variable name
  - dims/sizes
  - long name / description (if present)
  - units (if present)
  - GRIB step/stat qualifiers (if present)

## Summary mode (`--summary`)

- Number of opened datasets/groups
- Total raw variable count
- Per-group dims, coords, var counts
- Time coverage line (if present)

---

## Dependencies

Typical runtime dependencies:

- `xarray`
- `numpy`
- `fsspec`
- `cfgrib` (for GRIB)
- `ecCodes` runtime (required by `cfgrib`)

Optional/auth-related environment support:

- cloud SDK/credentials for `--auth` paths
- CDS API key for specific ECMWF HTTPS-backed access patterns

---

## Troubleshooting

## GRIB index or cfgrib errors

- Ensure file is truly GRIB/GRIB2.
- Ensure `cfgrib` + ecCodes are correctly installed.
- For arbitrary text/binary files, expect a clean unsupported-format error.

## Zarr consolidated metadata warning

If you see warnings about consolidated metadata fallback:
- For stores you control: consolidate metadata once -- use included script (python consolidate_zarr_metadata.py).
- For read-only/public stores: open with `consolidated=False` in custom workflows if needed.

## Cloud auth errors with `--auth`

If credentials are missing and data is public, rerun **without** `--auth`.

---

## Exit behavior

Current practical exit behavior:

- `0`: successful inventory or directory classification
- `1`: read/open/format errors (including unsupported format and missing path)


---

## Suggested next enhancements (optional)

- `--debug` open-path tracing
- `--max-vars N` output limiting
- `--format json` machine-readable output
- stricter CI-friendly warning handling

## Acknowledgment & Disclaimer

These scripts and documentation were developed with the assistance of an AI collaborator (Gemini) under human technical direction, supervision, review and testing. 

"""Validate a finalized calibration export and its Hugging Face sync records.

This module does not contact Hugging Face.  The scheduled finalizer runs the
``hf`` client and passes its saved plan and listing to these validators.
"""

import argparse
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath


MANIFEST_NAME = "export-manifest.json"
COMPLETE_MARKER_NAME = "EXPORT_COMPLETE"
RESERVED_PATHS = {MANIFEST_NAME, COMPLETE_MARKER_NAME}


def _canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        for block in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_atomic_new(path: Path, data: bytes) -> None:
    if path.exists():
        raise FileExistsError(
            "Finalized export metadata must not be overwritten; "
            f"expected absent path={path}"
        )
    temporary_path = path.with_name(f".{path.name}.tmp")
    if temporary_path.exists():
        raise FileExistsError(
            "Atomic export temporary path already exists; "
            f"expected absent path={temporary_path}"
        )
    with temporary_path.open("xb") as output_file:
        output_file.write(data)
        output_file.flush()
        os.fsync(output_file.fileno())
    os.replace(temporary_path, path)


def _safe_relative_path(path: object, context: str) -> str:
    if not isinstance(path, str) or not path:
        raise ValueError(f"{context} must be a nonempty string; observed={path!r}")
    if "\\" in path:
        raise ValueError(f"{context} must use POSIX separators; observed={path!r}")
    pure_path = PurePosixPath(path)
    if pure_path.is_absolute() or path.startswith("/"):
        raise ValueError(f"{context} must be relative; observed={path!r}")
    if any(part in {"", ".", ".."} for part in path.split("/")):
        raise ValueError(
            f"{context} contains an empty, current, or parent component; "
            f"observed={path!r}"
        )
    return path


def _files_in(directory: Path, excluded_paths: set[str]) -> dict[str, Path]:
    if not directory.is_dir():
        raise FileNotFoundError(
            "Export directory does not exist; "
            f"expected directory path={directory}"
        )

    files: dict[str, Path] = {}
    for path in sorted(directory.rglob("*")):
        relative_path = path.relative_to(directory).as_posix()
        _safe_relative_path(relative_path, "Export path")
        if path.is_symlink():
            raise ValueError(
                "Export must not contain symbolic links; "
                f"observed path={path}"
            )
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError(
                "Export must contain only regular files and directories; "
                f"observed path={path}"
            )
        if relative_path not in excluded_paths:
            files[relative_path] = path
    return files


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"JSON object contains duplicate key={key!r}")
        value[key] = item
    return value


def _load_json_text(text: str, context: str) -> object:
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"{context} is not valid JSON; line={error.lineno}, "
            f"column={error.colno}, message={error.msg}"
        ) from error


def _require_exact_keys(
    value: dict[str, object], required: set[str], optional: set[str], context: str
) -> None:
    missing = sorted(required - set(value))
    unexpected = sorted(set(value) - required - optional)
    if missing or unexpected:
        raise ValueError(
            f"{context} keys do not match the required schema; "
            f"missing={missing}, unexpected={unexpected}"
        )


def _load_manifest(export_dir: Path) -> dict[str, object]:
    manifest_path = export_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise FileNotFoundError(
            "Export manifest is missing; "
            f"expected file path={manifest_path}"
        )
    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest_text = manifest_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(
            f"Export manifest is not UTF-8; path={manifest_path}"
        ) from error
    manifest = _load_json_text(manifest_text, f"Export manifest path={manifest_path}")
    if not isinstance(manifest, dict):
        raise ValueError(
            "Export manifest must be one JSON object; "
            f"observed type={type(manifest).__name__}, path={manifest_path}"
        )
    _require_exact_keys(
        manifest,
        {"schema_version", "file_count", "total_size_bytes", "files"},
        set(),
        "Export manifest",
    )
    if (
        not isinstance(manifest["schema_version"], int)
        or isinstance(manifest["schema_version"], bool)
        or manifest["schema_version"] != 1
    ):
        raise ValueError(
            "Unsupported export manifest schema; expected schema_version=1, "
            f"observed={manifest['schema_version']!r}"
        )
    if _canonical_json_bytes(manifest) != manifest_bytes:
        raise ValueError(
            "Export manifest is not in canonical JSON form; "
            f"path={manifest_path}"
        )
    return manifest


def write_export_manifest(export_dir: Path | str) -> dict[str, object]:
    """Hash every payload file and atomically mark the export complete."""

    export_path = Path(export_dir)
    payload_files = _files_in(export_path, RESERVED_PATHS)
    if any((export_path / name).exists() for name in RESERVED_PATHS):
        raise FileExistsError(
            "Export finalization requires absent metadata files; "
            f"manifest={export_path / MANIFEST_NAME}, "
            f"marker={export_path / COMPLETE_MARKER_NAME}"
        )

    file_records = [
        {
            "path": relative_path,
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for relative_path, path in payload_files.items()
    ]
    manifest: dict[str, object] = {
        "schema_version": 1,
        "file_count": len(file_records),
        "total_size_bytes": sum(record["size_bytes"] for record in file_records),
        "files": file_records,
    }
    manifest_path = export_path / MANIFEST_NAME
    _write_atomic_new(manifest_path, _canonical_json_bytes(manifest))
    complete_marker = {"export_manifest_sha256": _sha256(manifest_path)}
    _write_atomic_new(
        export_path / COMPLETE_MARKER_NAME,
        _canonical_json_bytes(complete_marker),
    )
    return manifest


def verify_export(export_dir: Path | str) -> dict[str, object]:
    """Verify the completion marker, exact payload set, sizes, and SHA256 values."""

    export_path = Path(export_dir)
    manifest = _load_manifest(export_path)
    marker_path = export_path / COMPLETE_MARKER_NAME
    if not marker_path.is_file():
        raise FileNotFoundError(
            "Export completion marker is missing; "
            f"expected file path={marker_path}"
        )
    marker_bytes = marker_path.read_bytes()
    try:
        marker_text = marker_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(
            f"Export completion marker is not UTF-8; path={marker_path}"
        ) from error
    marker = _load_json_text(marker_text, f"Export completion marker path={marker_path}")
    if not isinstance(marker, dict):
        raise ValueError(
            "Export completion marker must be one JSON object; "
            f"observed type={type(marker).__name__}, path={marker_path}"
        )
    _require_exact_keys(
        marker,
        {"export_manifest_sha256"},
        set(),
        "Export completion marker",
    )
    if _canonical_json_bytes(marker) != marker_bytes:
        raise ValueError(
            "Export completion marker is not in canonical JSON form; "
            f"path={marker_path}"
        )
    expected_manifest_sha256 = _sha256(export_path / MANIFEST_NAME)
    if marker["export_manifest_sha256"] != expected_manifest_sha256:
        raise RuntimeError(
            "Export completion marker checksum differs from the manifest; "
            f"expected={expected_manifest_sha256}, "
            f"observed={marker['export_manifest_sha256']!r}, path={marker_path}"
        )

    file_records = manifest["files"]
    if not isinstance(file_records, list):
        raise ValueError(
            "Export manifest files must be a JSON list; "
            f"observed type={type(file_records).__name__}"
        )
    expected_files: dict[str, dict[str, object]] = {}
    for index, record in enumerate(file_records):
        if not isinstance(record, dict):
            raise ValueError(
                "Export manifest file record must be a JSON object; "
                f"index={index}, observed type={type(record).__name__}"
            )
        _require_exact_keys(
            record,
            {"path", "size_bytes", "sha256"},
            set(),
            f"Export manifest file record index={index}",
        )
        relative_path = _safe_relative_path(
            record["path"], f"Export manifest file path index={index}"
        )
        if relative_path in RESERVED_PATHS:
            raise ValueError(
                "Export manifest must not include its own metadata; "
                f"observed path={relative_path!r}"
            )
        if relative_path in expected_files:
            raise ValueError(
                "Export manifest contains a duplicate file path; "
                f"observed path={relative_path!r}"
            )
        size_bytes = record["size_bytes"]
        if (
            not isinstance(size_bytes, int)
            or isinstance(size_bytes, bool)
            or size_bytes < 0
        ):
            raise ValueError(
                "Export manifest size_bytes must be a nonnegative integer; "
                f"path={relative_path!r}, observed={size_bytes!r}"
            )
        sha256 = record["sha256"]
        if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ValueError(
                "Export manifest sha256 must be 64 lowercase hexadecimal characters; "
                f"path={relative_path!r}, observed={sha256!r}"
            )
        expected_files[relative_path] = record

    if list(expected_files) != sorted(expected_files):
        raise ValueError("Export manifest file records must be sorted by path")
    actual_files = _files_in(export_path, RESERVED_PATHS)
    if set(actual_files) != set(expected_files):
        raise RuntimeError(
            "Export payload file set differs from the manifest; "
            f"missing={sorted(set(expected_files) - set(actual_files))}, "
            f"unexpected={sorted(set(actual_files) - set(expected_files))}"
        )
    for relative_path, path in actual_files.items():
        record = expected_files[relative_path]
        observed_size = path.stat().st_size
        if observed_size != record["size_bytes"]:
            raise RuntimeError(
                "Export payload size differs from the manifest; "
                f"path={relative_path!r}, expected={record['size_bytes']}, "
                f"observed={observed_size}"
            )
        observed_sha256 = _sha256(path)
        if observed_sha256 != record["sha256"]:
            raise RuntimeError(
                "Export payload checksum differs from the manifest; "
                f"path={relative_path!r}, expected={record['sha256']}, "
                f"observed={observed_sha256}"
            )

    file_count = manifest["file_count"]
    total_size_bytes = manifest["total_size_bytes"]
    if (
        not isinstance(file_count, int)
        or isinstance(file_count, bool)
        or file_count != len(expected_files)
    ):
        raise RuntimeError(
            "Export manifest file_count is inconsistent; "
            f"expected={len(expected_files)}, observed={file_count!r}"
        )
    expected_total_size = sum(
        int(record["size_bytes"]) for record in expected_files.values()
    )
    if (
        not isinstance(total_size_bytes, int)
        or isinstance(total_size_bytes, bool)
        or total_size_bytes != expected_total_size
    ):
        raise RuntimeError(
            "Export manifest total_size_bytes is inconsistent; "
            f"expected={expected_total_size}, observed={total_size_bytes!r}"
        )
    return manifest


def _finalized_file_sizes(export_dir: Path) -> dict[str, int]:
    verify_export(export_dir)
    return {
        relative_path: path.stat().st_size
        for relative_path, path in _files_in(export_dir, set()).items()
    }


def _load_plan_lines(plan_path: Path) -> list[dict[str, object]]:
    if not plan_path.is_file():
        raise FileNotFoundError(
            f"Upload plan does not exist; expected file path={plan_path}"
        )
    plan_text = plan_path.read_text(encoding="utf-8")
    if not plan_text:
        raise ValueError(f"Upload plan is empty; path={plan_path}")
    if not plan_text.endswith("\n"):
        raise ValueError(f"Upload plan must end with a newline; path={plan_path}")
    lines = plan_text.splitlines()
    if any(not line.strip() for line in lines):
        raise ValueError(f"Upload plan contains a blank JSONL line; path={plan_path}")
    objects: list[dict[str, object]] = []
    for line_number, line in enumerate(lines, start=1):
        value = _load_json_text(line, f"Upload plan line={line_number}, path={plan_path}")
        if not isinstance(value, dict):
            raise ValueError(
                "Every upload plan JSONL line must be one object; "
                f"line={line_number}, observed type={type(value).__name__}"
            )
        objects.append(value)
    return objects


def _destination_prefix(destination_uri: str) -> str:
    protocol = "hf://buckets/"
    if not isinstance(destination_uri, str) or not destination_uri.startswith(protocol):
        raise ValueError(
            "Destination must be an hf://buckets URI; "
            f"observed destination={destination_uri!r}"
        )
    components = destination_uri[len(protocol) :].split("/")
    if len(components) < 2 or any(not component for component in components):
        raise ValueError(
            "Destination bucket URI contains an empty namespace, bucket, or path; "
            f"observed destination={destination_uri!r}"
        )
    for component in components:
        _safe_relative_path(component, "Destination URI component")
    return "/".join(components[2:])


def validate_upload_plan(
    plan_path: Path | str,
    source_dir: Path | str,
    destination_uri: str,
) -> dict[str, int]:
    """Require one upload operation for every finalized export file."""

    source_path = Path(source_dir)
    if not source_path.is_absolute():
        raise ValueError(
            "Upload-plan source directory must be absolute; "
            f"observed source={source_path}"
        )
    if source_path.resolve() != source_path:
        raise ValueError(
            "Upload-plan source directory must be a resolved absolute path; "
            f"observed={source_path}, resolved={source_path.resolve()}"
        )
    _destination_prefix(destination_uri)
    expected_sizes = _finalized_file_sizes(source_path)
    records = _load_plan_lines(Path(plan_path))
    header = records[0]
    _require_exact_keys(
        header,
        {"type", "source", "dest", "timestamp", "summary"},
        set(),
        "Upload plan header",
    )
    if header["type"] != "header":
        raise ValueError(
            "Upload plan first line must have type='header'; "
            f"observed={header['type']!r}"
        )
    if header["source"] != str(source_path):
        raise ValueError(
            "Upload plan source differs from the required absolute directory; "
            f"expected={str(source_path)!r}, observed={header['source']!r}"
        )
    if header["dest"] != destination_uri:
        raise ValueError(
            "Upload plan destination differs from the required URI; "
            f"expected={destination_uri!r}, observed={header['dest']!r}"
        )
    if not isinstance(header["timestamp"], str) or not header["timestamp"]:
        raise ValueError(
            "Upload plan timestamp must be a nonempty string; "
            f"observed={header['timestamp']!r}"
        )

    operations: dict[str, int] = {}
    total_size = 0
    for line_number, operation in enumerate(records[1:], start=2):
        _require_exact_keys(
            operation,
            {"type", "action", "path", "size", "reason"},
            {"local_mtime", "remote_mtime"},
            f"Upload plan operation line={line_number}",
        )
        if operation["type"] != "operation":
            raise ValueError(
                "Upload plan contains an unknown record type; "
                f"line={line_number}, observed={operation['type']!r}"
            )
        if operation["action"] != "upload":
            raise ValueError(
                "Upload plan permits upload operations only; "
                f"line={line_number}, observed action={operation['action']!r}"
            )
        relative_path = _safe_relative_path(
            operation["path"], f"Upload plan path line={line_number}"
        )
        if relative_path in operations:
            raise ValueError(
                "Upload plan contains a duplicate operation path; "
                f"observed path={relative_path!r}"
            )
        size = operation["size"]
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ValueError(
                "Upload operation size must be a nonnegative integer; "
                f"path={relative_path!r}, observed={size!r}"
            )
        if not isinstance(operation["reason"], str):
            raise ValueError(
                "Upload operation reason must be a string; "
                f"path={relative_path!r}, observed={operation['reason']!r}"
            )
        for time_key in ("local_mtime", "remote_mtime"):
            if time_key in operation and not isinstance(operation[time_key], str):
                raise ValueError(
                    f"Upload operation {time_key} must be a string; "
                    f"path={relative_path!r}, observed={operation[time_key]!r}"
                )
        operations[relative_path] = size
        total_size += size

    if operations != expected_sizes:
        wrong_sizes = sorted(
            path
            for path in set(operations) & set(expected_sizes)
            if operations[path] != expected_sizes[path]
        )
        raise RuntimeError(
            "Upload operations differ from the finalized export; "
            f"missing={sorted(set(expected_sizes) - set(operations))}, "
            f"unexpected={sorted(set(operations) - set(expected_sizes))}, "
            f"wrong_sizes={wrong_sizes}"
        )

    summary = header["summary"]
    if not isinstance(summary, dict):
        raise ValueError(
            "Upload plan summary must be one JSON object; "
            f"observed type={type(summary).__name__}"
        )
    _require_exact_keys(
        summary,
        {"uploads", "downloads", "deletes", "skips", "total_size"},
        set(),
        "Upload plan summary",
    )
    for key, value in summary.items():
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(
                "Upload plan summary values must be nonnegative integers; "
                f"key={key!r}, observed={value!r}"
            )
    expected_summary = {
        "uploads": len(operations),
        "downloads": 0,
        "deletes": 0,
        "skips": 0,
        "total_size": total_size,
    }
    if summary != expected_summary:
        raise RuntimeError(
            "Upload plan summary differs from its operations; "
            f"expected={expected_summary}, observed={summary}"
        )
    return expected_summary


def _remote_items(listing_path: Path) -> list[dict[str, object]]:
    if not listing_path.is_file():
        raise FileNotFoundError(
            f"Remote listing does not exist; expected file path={listing_path}"
        )
    listing_text = listing_path.read_text(encoding="utf-8")
    if not listing_text.strip():
        return []
    value = _load_json_text(listing_text, f"Remote listing path={listing_path}")
    if not isinstance(value, list):
        raise ValueError(
            "Remote listing must be one JSON array; "
            f"observed type={type(value).__name__}, path={listing_path}"
        )
    items: list[dict[str, object]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(
                "Remote listing item must be one JSON object; "
                f"index={index}, observed type={type(item).__name__}"
            )
        items.append(item)
    return items


def validate_remote_listing(
    listing_path: Path | str,
    export_dir: Path | str,
    destination_uri: str,
) -> dict[str, int]:
    """Compare an ``hf buckets list -R --format json`` result to local files."""

    export_path = Path(export_dir)
    expected_sizes = {
        relative_path: path.stat().st_size
        for relative_path, path in _files_in(export_path, set()).items()
    }
    prefix = _destination_prefix(destination_uri)
    items = _remote_items(Path(listing_path))
    if not items:
        if expected_sizes:
            raise RuntimeError(
                "Remote listing is empty but the export contains files; "
                f"expected file_count={len(expected_sizes)}"
            )
        return {"file_count": 0, "total_size_bytes": 0}

    remote_files: dict[str, int] = {}
    remote_directories: set[str] = set()
    for index, item in enumerate(items):
        item_type = item.get("type")
        if item_type == "file":
            _require_exact_keys(
                item,
                {"type", "path", "size", "xet_hash"},
                {"mtime", "uploaded_at"},
                f"Remote file listing item index={index}",
            )
            size = item["size"]
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise ValueError(
                    "Remote file size must be a nonnegative integer; "
                    f"index={index}, observed={size!r}"
                )
            if not isinstance(item["xet_hash"], str) or not item["xet_hash"]:
                raise ValueError(
                    "Remote file xet_hash must be a nonempty string; "
                    f"index={index}, observed={item['xet_hash']!r}"
                )
            remote_path = _safe_relative_path(
                item["path"], f"Remote file path index={index}"
            )
            if remote_path in remote_files or remote_path in remote_directories:
                raise ValueError(
                    "Remote listing contains a duplicate path; "
                    f"observed path={remote_path!r}"
                )
            remote_files[remote_path] = size
        elif item_type == "directory":
            _require_exact_keys(
                item,
                {"type", "path"},
                {"uploaded_at"},
                f"Remote directory listing item index={index}",
            )
            remote_path = _safe_relative_path(
                item["path"], f"Remote directory path index={index}"
            )
            if remote_path in remote_files or remote_path in remote_directories:
                raise ValueError(
                    "Remote listing contains a duplicate path; "
                    f"observed path={remote_path!r}"
                )
            remote_directories.add(remote_path)
        else:
            raise ValueError(
                "Remote listing contains an unknown item type; "
                f"index={index}, observed={item_type!r}"
            )

    def relative_paths(rooted: bool) -> tuple[dict[str, int], set[str]] | None:
        prefix_with_slash = f"{prefix}/" if prefix else ""
        paths = set(remote_files) | remote_directories
        if rooted and (not prefix or any(not path.startswith(prefix_with_slash) for path in paths)):
            return None
        if rooted:
            files = {
                path[len(prefix_with_slash) :]: size
                for path, size in remote_files.items()
            }
            directories = {
                path[len(prefix_with_slash) :] for path in remote_directories
            }
        else:
            files = dict(remote_files)
            directories = set(remote_directories)
        if any(not path for path in set(files) | directories):
            return None
        return files, directories

    candidates = [relative_paths(False), relative_paths(True)]
    matching_candidate: tuple[dict[str, int], set[str]] | None = None
    for candidate in candidates:
        if candidate is None:
            continue
        files, directories = candidate
        allowed_directories = {
            parent.as_posix()
            for path in expected_sizes
            for parent in PurePosixPath(path).parents
            if parent.as_posix() != "."
        }
        if files == expected_sizes and directories <= allowed_directories:
            matching_candidate = candidate
            break
    if matching_candidate is None:
        raise RuntimeError(
            "Remote file listing differs from the export file set or sizes; "
            f"destination={destination_uri!r}, expected={expected_sizes}, "
            f"observed={remote_files}"
        )
    return {
        "file_count": len(expected_sizes),
        "total_size_bytes": sum(expected_sizes.values()),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Finalize and validate private artifact-store exports."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    write_parser = subparsers.add_parser("write-export")
    write_parser.add_argument("--export-dir", type=Path, required=True)

    verify_parser = subparsers.add_parser("verify-export")
    verify_parser.add_argument("--export-dir", type=Path, required=True)

    plan_parser = subparsers.add_parser("validate-upload-plan")
    plan_parser.add_argument("--plan-path", type=Path, required=True)
    plan_parser.add_argument("--source-dir", type=Path, required=True)
    plan_parser.add_argument("--destination-uri", required=True)

    listing_parser = subparsers.add_parser("validate-remote-listing")
    listing_parser.add_argument("--listing-path", type=Path, required=True)
    listing_parser.add_argument("--export-dir", type=Path, required=True)
    listing_parser.add_argument("--destination-uri", required=True)
    return parser


def main() -> None:
    arguments = _parser().parse_args()
    if arguments.command == "write-export":
        manifest = write_export_manifest(arguments.export_dir)
        result = {
            "file_count": manifest["file_count"],
            "total_size_bytes": manifest["total_size_bytes"],
        }
    elif arguments.command == "verify-export":
        manifest = verify_export(arguments.export_dir)
        result = {
            "file_count": manifest["file_count"],
            "total_size_bytes": manifest["total_size_bytes"],
        }
    elif arguments.command == "validate-upload-plan":
        result = validate_upload_plan(
            arguments.plan_path,
            arguments.source_dir,
            arguments.destination_uri,
        )
    elif arguments.command == "validate-remote-listing":
        result = validate_remote_listing(
            arguments.listing_path,
            arguments.export_dir,
            arguments.destination_uri,
        )
    else:
        raise RuntimeError(f"Unhandled backup command={arguments.command!r}")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()

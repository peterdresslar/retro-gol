import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from retro_gol.backup import (
    validate_remote_listing,
    validate_upload_plan,
    verify_export,
    write_export_manifest,
)


DESTINATION_URI = (
    "hf://buckets/peterdresslar/retro-gol-private/"
    "calibrations/test-export"
)


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"


class BackupTests(unittest.TestCase):
    def make_export(self, directory: Path) -> Path:
        export_dir = (directory / "export").resolve()
        (export_dir / "nested").mkdir(parents=True)
        (export_dir / "a.txt").write_bytes(b"alpha")
        (export_dir / "nested" / "b.bin").write_bytes(b"\x00\x01\x02")
        write_export_manifest(export_dir)
        return export_dir

    def expected_sizes(self, export_dir: Path) -> dict[str, int]:
        return {
            path.relative_to(export_dir).as_posix(): path.stat().st_size
            for path in sorted(export_dir.rglob("*"))
            if path.is_file()
        }

    def write_upload_plan(
        self,
        plan_path: Path,
        export_dir: Path,
        *,
        action: str = "upload",
        summary_change: dict[str, int] | None = None,
        operation_change: dict[str, object] | None = None,
    ) -> None:
        sizes = self.expected_sizes(export_dir)
        operations = [
            {
                "type": "operation",
                "action": action,
                "path": path,
                "size": size,
                "reason": "new file",
                "local_mtime": "2026-07-23T00:00:00+00:00",
            }
            for path, size in sizes.items()
        ]
        if operation_change:
            operations[0].update(operation_change)
        summary = {
            "uploads": len(operations) if action == "upload" else 0,
            "downloads": len(operations) if action == "download" else 0,
            "deletes": len(operations) if action == "delete" else 0,
            "skips": len(operations) if action == "skip" else 0,
            "total_size": (
                sum(item["size"] for item in operations)
                if action in {"upload", "download"}
                else 0
            ),
        }
        if summary_change:
            summary.update(summary_change)
        records = [
            {
                "type": "header",
                "source": str(export_dir),
                "dest": DESTINATION_URI,
                "timestamp": "2026-07-23T00:00:00+00:00",
                "summary": summary,
            },
            *operations,
        ]
        plan_path.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )

    def remote_items(self, export_dir: Path, rooted: bool = True) -> list[dict[str, object]]:
        prefix = "calibrations/test-export"
        return [
            {
                "type": "file",
                "path": f"{prefix}/{path}" if rooted else path,
                "size": size,
                "xet_hash": "xet-hash",
                "mtime": "2026-07-23T00:00:00+00:00",
            }
            for path, size in self.expected_sizes(export_dir).items()
        ]

    def test_manifest_is_stable_and_detects_payload_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            export_dir = self.make_export(Path(temporary_directory))
            manifest = verify_export(export_dir)
            self.assertEqual(manifest["file_count"], 2)
            self.assertEqual(manifest["total_size_bytes"], 8)
            self.assertEqual(
                [record["path"] for record in manifest["files"]],
                ["a.txt", "nested/b.bin"],
            )
            manifest_sha256 = hashlib.sha256(
                (export_dir / "export-manifest.json").read_bytes()
            ).hexdigest()
            marker = json.loads(
                (export_dir / "EXPORT_COMPLETE").read_text(encoding="utf-8")
            )
            self.assertEqual(marker["export_manifest_sha256"], manifest_sha256)

            (export_dir / "a.txt").write_bytes(b"changed")
            with self.assertRaisesRegex(RuntimeError, "size differs"):
                verify_export(export_dir)

    def test_manifest_rejects_noncanonical_and_unsafe_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            export_dir = self.make_export(Path(temporary_directory))
            manifest_path = export_dir / "export-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"][0]["path"] = "../a.txt"
            manifest_path.write_text(canonical_json(manifest), encoding="utf-8")
            marker = {
                "export_manifest_sha256": hashlib.sha256(
                    manifest_path.read_bytes()
                ).hexdigest()
            }
            (export_dir / "EXPORT_COMPLETE").write_text(
                canonical_json(marker), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "parent component"):
                verify_export(export_dir)

    def test_valid_upload_plan_matches_every_finalized_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            export_dir = self.make_export(directory)
            plan_path = directory / "upload-plan.jsonl"
            self.write_upload_plan(plan_path, export_dir)

            summary = validate_upload_plan(
                plan_path,
                export_dir,
                DESTINATION_URI,
            )
            self.assertEqual(summary["uploads"], 4)
            self.assertEqual(summary["downloads"], 0)

    def test_upload_plan_rejects_skip_unknown_key_and_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            export_dir = self.make_export(directory)
            plan_path = directory / "upload-plan.jsonl"

            self.write_upload_plan(plan_path, export_dir, action="skip")
            with self.assertRaisesRegex(ValueError, "upload operations only"):
                validate_upload_plan(plan_path, export_dir, DESTINATION_URI)

            self.write_upload_plan(
                plan_path,
                export_dir,
                operation_change={"unreviewed": True},
            )
            with self.assertRaisesRegex(ValueError, "unreviewed"):
                validate_upload_plan(plan_path, export_dir, DESTINATION_URI)

            self.write_upload_plan(
                plan_path,
                export_dir,
                operation_change={"path": "../escape"},
            )
            with self.assertRaisesRegex(ValueError, "parent component"):
                validate_upload_plan(plan_path, export_dir, DESTINATION_URI)

    def test_upload_plan_rejects_duplicate_and_incorrect_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            export_dir = self.make_export(directory)
            plan_path = directory / "upload-plan.jsonl"
            self.write_upload_plan(plan_path, export_dir)
            lines = plan_path.read_text(encoding="utf-8").splitlines()
            plan_path.write_text(
                "\n".join([*lines, lines[1]]) + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "duplicate operation path"):
                validate_upload_plan(plan_path, export_dir, DESTINATION_URI)

            self.write_upload_plan(
                plan_path,
                export_dir,
                summary_change={"uploads": 999},
            )
            with self.assertRaisesRegex(RuntimeError, "summary differs"):
                validate_upload_plan(plan_path, export_dir, DESTINATION_URI)

    def test_remote_listing_accepts_rooted_or_relative_exact_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            export_dir = self.make_export(directory)
            listing_path = directory / "remote-listing.json"
            listing_path.write_text(
                json.dumps(self.remote_items(export_dir, rooted=True)),
                encoding="utf-8",
            )
            result = validate_remote_listing(
                listing_path,
                export_dir,
                DESTINATION_URI,
            )
            self.assertEqual(result["file_count"], 4)

            listing_path.write_text(
                json.dumps(self.remote_items(export_dir, rooted=False)),
                encoding="utf-8",
            )
            validate_remote_listing(listing_path, export_dir, DESTINATION_URI)

    def test_remote_listing_rejects_wrong_size_and_unsafe_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            export_dir = self.make_export(directory)
            listing_path = directory / "remote-listing.json"
            items = self.remote_items(export_dir)
            items[0]["size"] += 1
            listing_path.write_text(json.dumps(items), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "differs from the export"):
                validate_remote_listing(
                    listing_path,
                    export_dir,
                    DESTINATION_URI,
                )

            items = self.remote_items(export_dir)
            items[0]["path"] = "../escape"
            listing_path.write_text(json.dumps(items), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "parent component"):
                validate_remote_listing(
                    listing_path,
                    export_dir,
                    DESTINATION_URI,
                )

    def test_empty_remote_stdout_is_valid_only_for_empty_expected_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            empty_export = directory / "empty-export"
            empty_export.mkdir()
            listing_path = directory / "remote-listing.json"
            listing_path.write_text("", encoding="utf-8")
            result = validate_remote_listing(
                listing_path,
                empty_export,
                DESTINATION_URI,
            )
            self.assertEqual(result["file_count"], 0)

            export_dir = self.make_export(directory)
            with self.assertRaisesRegex(RuntimeError, "listing is empty"):
                validate_remote_listing(
                    listing_path,
                    export_dir,
                    DESTINATION_URI,
                )


if __name__ == "__main__":
    unittest.main()

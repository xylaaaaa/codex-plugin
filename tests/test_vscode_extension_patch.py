import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from codex_fast_patch.bundle import patch_js
from codex_fast_patch.vscode import (
    VscodeExtensionPaths,
    backup_vscode_extension,
    detect_vscode_extension_paths,
    rollback_vscode_extension,
)


class VscodeExtensionPatchTest(unittest.TestCase):
    def make_extension(self, root: Path, version: str) -> Path:
        extension = root / f"openai.chatgpt-{version}-linux-x64"
        assets = extension / "webview" / "assets"
        assets.mkdir(parents=True)
        (extension / "package.json").write_text('{"name":"chatgpt"}', encoding="utf-8")
        return extension

    def write_patchable_assets(self, extension: Path) -> None:
        assets = extension / "webview" / "assets"
        (assets / "read-service-tier-for-request-a.js").write_text(
            "async function p(e,t){let n=await u(e,t);return n===`chatgpt`?"
            "(await e.query.fetch(c,{authMethod:n,hostId:t})).requirements?.featureRequirements?.fast_mode!==!1:!1}",
            encoding="utf-8",
        )
        (assets / "use-service-tier-settings-a.js").write_text(
            "let a=i?.authMethod===`chatgpt`,u=!!i?.isLoading||a&&l,"
            "d=a&&!u&&c!=null&&c?.requirements?.featureRequirements?.fast_mode!==!1",
            encoding="utf-8",
        )
        (assets / "use-plugins-a.js").write_text(
            "function he(e){return e!==`chatgpt`}"
            "function Pe(e,{isComputerUseAvailable:t,isExternalBrowserUseAvailable:n,isInAppBrowserUseAvailable:r})"
            "{return!(!r&&Le(e)||!n&&Re(e)||!t&&ze(e))}",
            encoding="utf-8",
        )
        (assets / "check-plugin-availability-a.js").write_text(
            "(r||n!=null&&!n.isPending&&n.error==null&&n.data==null)&&(i=`connector-unavailable`)",
            encoding="utf-8",
        )

    def test_detects_newest_vscode_server_extension(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            home = Path(raw_tmp)
            old_root = home / ".vscode-server" / "extensions"
            new_root = home / ".vscode-server" / "extensions"
            self.make_extension(old_root, "26.527.60818")
            newest = self.make_extension(new_root, "26.601.21317")

            paths = detect_vscode_extension_paths(home=home)

            self.assertEqual(paths.extension_dir, newest)
            self.assertEqual(paths.assets_dir, newest / "webview" / "assets")

    def test_patches_vscode_extension_bundle_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            extension = self.make_extension(Path(raw_tmp), "26.601.21317")
            self.write_patchable_assets(extension)
            paths = VscodeExtensionPaths(extension)

            report = patch_js(paths)

            self.assertEqual(report.patch_actions, 5)
            self.assertEqual(report.patched_files, 4)
            self.assertIn(
                "async function p(e,t){return true}",
                (paths.assets_dir / "read-service-tier-for-request-a.js").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "u=!!i?.isLoading,d=!u",
                (paths.assets_dir / "use-service-tier-settings-a.js").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "function he(e){return false}",
                (paths.assets_dir / "use-plugins-a.js").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "return!(!r&&Le(e)||!t&&ze(e))",
                (paths.assets_dir / "use-plugins-a.js").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "false&&(i=`connector-unavailable`)",
                (paths.assets_dir / "check-plugin-availability-a.js").read_text(encoding="utf-8"),
            )

    def test_vscode_backup_and_rollback_restore_modified_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            extension = self.make_extension(Path(raw_tmp), "26.601.21317")
            self.write_patchable_assets(extension)
            paths = VscodeExtensionPaths(extension)

            backup_vscode_extension(paths)
            original = (paths.assets_dir / "read-service-tier-for-request-a.js").read_text(encoding="utf-8")
            (paths.assets_dir / "read-service-tier-for-request-a.js").write_text("patched", encoding="utf-8")

            rollback_vscode_extension(paths)

            self.assertEqual(
                (paths.assets_dir / "read-service-tier-for-request-a.js").read_text(encoding="utf-8"),
                original,
            )
            self.assertFalse(paths.backup_dir.exists())

    def test_vscode_backup_refuses_to_overwrite_existing_backup(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            extension = self.make_extension(Path(raw_tmp), "26.601.21317")
            self.write_patchable_assets(extension)
            paths = VscodeExtensionPaths(extension)
            paths.backup_dir.mkdir()

            with self.assertRaises(SystemExit):
                backup_vscode_extension(paths)


if __name__ == "__main__":
    unittest.main()

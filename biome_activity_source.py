import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LATEST_RELEASE_API = "https://api.github.com/repos/xVapure/Noteab-Macro/releases/latest"
USER_AGENT = "noteab-linux-macro-bootstrapper"

LINUX_ASSET_CANDIDATES = (
    "CoteabMacro.AppImage",
    "CoteabMacro-linux.AppImage",
    "CoteabMacro-Linux.AppImage",
    "CoteabMacro-x86_64.AppImage",
    "CoteabMacro-linux-x86_64.AppImage",
    "CoteabMacro-linux.tar.gz",
    "CoteabMacro-Linux.tar.gz",
    "CoteabMacro-linux.zip",
    "CoteabMacro-Linux.zip",
    "CoteabMacro",
    # Keep the Windows build as the last Linux fallback so the current release can run through Wine.
    "CoteabMacro.exe",
)

ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz")
EXECUTABLE_SUFFIXES = ("", ".AppImage", ".exe")


def require_linux() -> None:
    system_name = platform.system()
    if system_name != "Linux":
        raise RuntimeError(
            f"This bootstrapper is Linux-only. Detected unsupported OS: {system_name or 'unknown'}."
        )


def get_asset_candidates() -> tuple[str, ...]:
    """Return Linux asset candidates, allowing users to override with COTEAB_ASSET_NAME."""
    override_asset = os.environ.get("COTEAB_ASSET_NAME")
    if override_asset:
        return (override_asset,)
    return LINUX_ASSET_CANDIDATES


def get_latest_release_data() -> dict:
    request = Request(
        LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
        },
    )
    with urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"GitHub API returned HTTP {response.status}")
        return json.load(response)


def find_asset(release_data: dict, asset_candidates: tuple[str, ...]) -> dict:
    assets = release_data.get("assets", [])
    assets_by_name = {asset.get("name"): asset for asset in assets}
    for asset_name in asset_candidates:
        asset = assets_by_name.get(asset_name)
        if asset and asset.get("browser_download_url"):
            return asset

    available_assets = ", ".join(sorted(name for name in assets_by_name if name)) or "none"
    expected_assets = ", ".join(asset_candidates)
    raise RuntimeError(
        "No Linux-compatible release asset was found. "
        f"Expected one of: {expected_assets}. Available assets: {available_assets}."
    )


def download_file(url: str, output_path: Path) -> None:
    request = Request(
        url,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": USER_AGENT,
        },
    )
    with urlopen(request, timeout=120) as response, output_path.open("wb") as output_file:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            output_file.write(chunk)


def is_archive(path: Path) -> bool:
    name = path.name.lower()
    return any(name.endswith(suffix) for suffix in ARCHIVE_SUFFIXES)


def ensure_executable(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def looks_executable(path: Path) -> bool:
    if path.is_dir():
        return False
    if path.suffix in EXECUTABLE_SUFFIXES:
        return True
    return os.access(path, os.X_OK)


def extract_archive(archive_path: Path, destination_dir: Path) -> Path:
    extract_dir = destination_dir / archive_path.stem.replace(".tar", "")
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True)

    if archive_path.name.lower().endswith(".zip"):
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(extract_dir)
    else:
        with tarfile.open(archive_path) as archive:
            archive.extractall(extract_dir, filter="data")

    executable_matches = [
        path
        for path in extract_dir.rglob("*")
        if path.name.startswith("CoteabMacro") and looks_executable(path)
    ]
    if not executable_matches:
        executable_matches = [path for path in extract_dir.rglob("*") if looks_executable(path)]
    if not executable_matches:
        raise RuntimeError(f"No executable file was found inside {archive_path.name}")

    executable_path = executable_matches[0]
    ensure_executable(executable_path)
    return executable_path


def prepare_downloaded_asset(asset_path: Path, app_dir: Path) -> Path:
    if is_archive(asset_path):
        return extract_archive(asset_path, app_dir)
    ensure_executable(asset_path)
    return asset_path


def get_run_command(executable_path: Path) -> list[str]:
    if executable_path.suffix.lower() != ".exe":
        return [str(executable_path)]

    wine_path = shutil.which("wine")
    if not wine_path:
        raise RuntimeError(
            f"{executable_path.name} is a Windows executable fallback, but Wine is not installed. "
            "Install Wine (for example: sudo apt install wine) or publish a native Linux asset."
        )

    print(f"No native Linux asset was selected; launching {executable_path.name} with Wine.")
    return [wine_path, str(executable_path)]


def run_executable(executable_path: Path) -> None:
    subprocess.Popen(get_run_command(executable_path), cwd=str(executable_path.parent))


def main() -> int:
    try:
        require_linux()
        asset_candidates = get_asset_candidates()
    except RuntimeError as error:
        print(error)
        return 1

    print(
        "Downloading the latest Coteab Macro release for Linux. "
        f"Looking for: {', '.join(asset_candidates)}"
    )

    try:
        release_data = get_latest_release_data()
        asset = find_asset(release_data, asset_candidates)
    except (HTTPError, URLError, RuntimeError, json.JSONDecodeError) as error:
        print(f"Failed to get release information: {error}")
        return 1

    app_dir = Path(__file__).resolve().parent
    output_path = app_dir / asset["name"]
    try:
        download_file(asset["browser_download_url"], output_path)
        executable_path = prepare_downloaded_asset(output_path, app_dir)
    except (HTTPError, URLError, OSError, RuntimeError, tarfile.TarError, zipfile.BadZipFile) as error:
        print(f"Failed to download or prepare {asset['name']}: {error}")
        return 1

    try:
        run_executable(executable_path)
    except (OSError, RuntimeError) as error:
        print(f"Downloaded but failed to run {executable_path}: {error}")
        return 1

    print(f"Downloaded and launched: {executable_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

import tarfile
from pathlib import Path


def pack_world(root: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(dest, "w:gz") as archive:
        archive.add(root, arcname=root.name, filter=_skip_junk)
    return dest


def _skip_junk(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    name = info.name.replace("\\", "/")
    parts = name.split("/")
    if "__pycache__" in parts or ".pytest_cache" in parts:
        return None
    if parts and parts[-1] in {".DS_Store"}:
        return None
    return info

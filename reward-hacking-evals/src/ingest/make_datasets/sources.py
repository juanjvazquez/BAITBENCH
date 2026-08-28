"""Pluggable run sources for make_datasets ingest.

A RunSource is whatever lets `postrun.py` see ``<runs_base>/<run_id>/...`` as a
filesystem path. Modal apps mount their input volume to /runs and call postrun
with --remote-base /runs/<sub>; laptop scripts use a local path. The two cases
look identical to postrun.

This module provides a small URI parser so orchestrator scripts can accept
either form on the command line:

    local:///abs/path/to/run_bundles
    modal://<volume-name>/<sub>

`SSHSource` is intentionally NOT included: per the modal-only execution
backend, raw run dirs are mirrored to a Modal volume by `mirror_into_modal.py`
before any pipeline step touches them. SSH access is only used for the mirror
step itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Union
from urllib.parse import urlparse


SourceKind = Literal["local", "modal"]


@dataclass(frozen=True)
class LocalDirSource:
    """A filesystem path the caller can pass directly as --remote-base."""

    path: Path

    kind: SourceKind = "local"

    def remote_base(self) -> str:
        return str(self.path)


@dataclass(frozen=True)
class ModalVolumeSource:
    """A modal.Volume name + subdir.

    Inside a Modal container, the volume is mounted at /runs (or wherever the
    Modal app declares). Outside a Modal container, this descriptor only carries
    the metadata; the orchestrator is responsible for setting up the mount.
    """

    volume_name: str
    subdir: str = ""

    kind: SourceKind = "modal"

    def remote_base(self, mount_point: str = "/runs") -> str:
        if self.subdir:
            return f"{mount_point.rstrip('/')}/{self.subdir.strip('/')}"
        return mount_point.rstrip("/")


RunSource = Union[LocalDirSource, ModalVolumeSource]


def parse_source_uri(uri: str) -> RunSource:
    """Parse a --source-uri value.

    Accepts:
      local:///abs/path           -> LocalDirSource
      /abs/path                   -> LocalDirSource (bare path, treated as local)
      ./relative/path             -> LocalDirSource (bare path)
      modal://<volume>/<sub>      -> ModalVolumeSource

    Raises ValueError on unknown schemes.
    """
    if uri.startswith("modal://"):
        parsed = urlparse(uri)
        volume = parsed.netloc
        subdir = parsed.path.lstrip("/")
        if not volume:
            raise ValueError(f"modal:// URI missing volume name: {uri!r}")
        return ModalVolumeSource(volume_name=volume, subdir=subdir)
    if uri.startswith("local://"):
        # local:///abs/path  -> path = "/abs/path"
        # local://./rel/path -> netloc="." path="/rel/path" => "./rel/path"
        parsed = urlparse(uri)
        # Treat netloc as the leading segment when present (handles "./..").
        path_part = parsed.path
        if parsed.netloc and parsed.netloc != "":
            path_part = parsed.netloc + path_part
        return LocalDirSource(path=Path(path_part).expanduser())
    if uri.startswith("ssh://"):
        raise ValueError(
            "ssh:// sources are not supported by the pipeline. Use the mirror "
            "step (scripts/mirror_into_modal.py) to copy raw run dirs into a "
            "Modal volume first, then pass modal://<volume>/<sub>."
        )
    if "://" in uri:
        raise ValueError(f"unknown source URI scheme: {uri!r}")
    # Bare path (relative or absolute).
    return LocalDirSource(path=Path(uri).expanduser())


__all__ = [
    "LocalDirSource",
    "ModalVolumeSource",
    "RunSource",
    "parse_source_uri",
]

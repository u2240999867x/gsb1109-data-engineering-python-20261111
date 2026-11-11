import os
import re

from typing import TYPE_CHECKING

from tomlkit.api import loads
from tomlkit.toml_document import TOMLDocument


if TYPE_CHECKING:
    from _typeshed import StrPath as _StrPath
else:
    from typing import Union

    _StrPath = Union[str, os.PathLike]


class TOMLFile:
    """
    Represents a TOML file.

    :param path: path to the TOML file
    """

    def __init__(self, path: _StrPath) -> None:
        self._path = path
        self._linesep: str = os.linesep

    def read(self, *, track_sources: bool = False) -> TOMLDocument:
        """Read the file content as a :class:`tomlkit.toml_document.TOMLDocument`.

        :param track_sources: opt in to recording source spans, forwarded to
            :func:`tomlkit.loads`. The spans describe the (normalized,
            line-ending-adjusted) text that is actually parsed.
        """
        with open(self._path, encoding="utf-8", newline="") as f:
            content = f.read()

            # check if consistent line endings
            num_newline = content.count("\n")
            if num_newline > 0:
                num_win_eol = content.count("\r\n")
                if num_win_eol == num_newline:
                    self._linesep = "\r\n"
                    content = content.replace("\r\n", "\n")
                elif num_win_eol == 0:
                    self._linesep = "\n"
                else:
                    self._linesep = "mixed"

            return loads(content, track_sources=track_sources)

    def write(self, data: TOMLDocument) -> None:
        """Write the TOMLDocument to the file."""
        content = data.as_string()

        # apply linesep
        if self._linesep == "\n":
            content = content.replace("\r\n", "\n")
        elif self._linesep == "\r\n":
            content = re.sub(r"(?<!\r)\n", "\r\n", content)

        with open(self._path, "w", encoding="utf-8", newline="") as f:
            f.write(content)

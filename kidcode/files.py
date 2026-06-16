"""Project-scoped file service."""

import os


class FileService:
    def __init__(self, project_path=None, permissions=None):
        self.project_path = project_path
        self.permissions = permissions

    def _path(self, name):
        if self.permissions is not None:
            self.permissions.require_project_files()
        if self.project_path is None:
            raise RuntimeError("project files are not mounted")
        full = os.path.abspath(os.path.join(self.project_path, name))
        root = os.path.abspath(self.project_path)
        if full != root and not full.startswith(root + os.sep):
            raise PermissionError("file path is outside the project")
        return full

    def read_text(self, name):
        with open(self._path(name), "r", encoding="utf-8") as fh:
            return fh.read()

    def write_text(self, name, value):
        path = self._path(name)
        parent = os.path.dirname(path)
        if parent and not os.path.exists(parent):
            os.makedirs(parent)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(value)

    def list(self, name="."):
        return sorted(os.listdir(self._path(name)))

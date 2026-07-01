"""PC-side portable subset checker for kid project code."""

import ast
import os

from moybyte.manifest import Manifest, resolve_project_file

ALLOWED_IMPORTS = ["moybyte", "math", "random"]
DISALLOWED_CALLS = [
    "__import__",
    "compile",
    "dir",
    "eval",
    "exec",
    "getattr",
    "globals",
    "input",
    "locals",
    "open",
    "setattr",
    "vars",
]


class PortableIssue:
    def __init__(self, path, line, message):
        self.path = path
        self.line = line
        self.message = message

    def __str__(self):
        return self.path + ":" + str(self.line) + ": " + self.message


def _root_module(name):
    return name.split(".", 1)[0]


def _is_allowed_import(name):
    return _root_module(name) in ALLOWED_IMPORTS


def check_source(source, path):
    issues = []
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return [PortableIssue(path, exc.lineno or 1, "syntax error: " + exc.msg)]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not _is_allowed_import(alias.name):
                    issues.append(
                        PortableIssue(
                            path,
                            node.lineno,
                            "import '" + alias.name + "' is not in the portable Moybyte subset",
                        )
                    )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                issues.append(
                    PortableIssue(path, node.lineno, "relative imports are not portable in kid projects")
                )
            elif not _is_allowed_import(module):
                issues.append(
                    PortableIssue(
                        path,
                        node.lineno,
                        "import from '" + module + "' is not in the portable Moybyte subset",
                    )
                )
        elif isinstance(node, ast.Call):
            name = None
            if isinstance(node.func, ast.Name):
                name = node.func.id
            if name in DISALLOWED_CALLS:
                issues.append(
                    PortableIssue(
                        path,
                        node.lineno,
                        "call to '" + name + "' is not in the portable Moybyte subset",
                    )
                )
    return issues


def check_file(path):
    with open(path, "r", encoding="utf-8") as fh:
        return check_source(fh.read(), path)


def _project_python_files(project_path):
    manifest = Manifest.load(project_path)
    entry = resolve_project_file(project_path, manifest.entry, "entry")
    seen = set([os.path.abspath(entry)])
    yield entry
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [name for name in dirs if name not in ["assets", "__pycache__"]]
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.abspath(os.path.join(root, name))
            if path in seen:
                continue
            seen.add(path)
            yield path


def check_path(path):
    if os.path.isdir(path):
        issues = []
        for file_path in _project_python_files(path):
            issues.extend(check_file(file_path))
        return issues
    return check_file(path)

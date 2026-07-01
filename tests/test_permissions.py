import pytest

from moybyte.errors import PermissionDenied
from moybyte.files import FileService
from moybyte.permissions import Permissions


def test_audio_permission_denied():
    permissions = Permissions(audio=False)

    with pytest.raises(PermissionDenied):
        permissions.require_audio()


def test_radio_permission_denied():
    permissions = Permissions(radio=False)

    with pytest.raises(PermissionDenied):
        permissions.require_radio()


def test_file_service_rejects_path_escape(tmp_path):
    service = FileService(str(tmp_path), Permissions(files="project"))

    with pytest.raises(PermissionError):
        service.read_text("../outside.txt")

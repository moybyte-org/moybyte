"""Permission model for Moybyte projects."""

from .errors import ManifestError, PermissionDenied

FILE_MODES = ["none", "project", "sd_read", "sd_write", "advanced"]


class Permissions:
    def __init__(
        self,
        files="project",
        sd_card=False,
        audio=True,
        radio=False,
        wifi=False,
        ai=False,
        gpio=False,
        system=False,
    ):
        if files not in FILE_MODES:
            raise ManifestError("permissions.files must be one of: " + ", ".join(FILE_MODES))
        self.files = files
        self.sd_card = bool(sd_card)
        self.audio = bool(audio)
        self.radio = bool(radio)
        self.wifi = bool(wifi)
        self.ai = bool(ai)
        self.gpio = gpio
        self.system = bool(system)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ManifestError("permissions must be an object")
        return cls(
            files=data.get("files", "project"),
            sd_card=data.get("sd_card", False),
            audio=data.get("audio", True),
            radio=data.get("radio", False),
            wifi=data.get("wifi", False),
            ai=data.get("ai", False),
            gpio=data.get("gpio", False),
            system=data.get("system", False),
        )

    def to_dict(self):
        return {
            "files": self.files,
            "sd_card": self.sd_card,
            "audio": self.audio,
            "radio": self.radio,
            "wifi": self.wifi,
            "ai": self.ai,
            "gpio": self.gpio,
            "system": self.system,
        }

    def require_audio(self):
        if not self.audio:
            raise PermissionDenied(
                "This project tried to use audio, but audio is not enabled for this project."
            )

    def require_radio(self):
        if not self.radio:
            raise PermissionDenied(
                "This project tried to use radio, but radio is not enabled for this project."
            )

    def require_project_files(self):
        if self.files == "none":
            raise PermissionDenied(
                "This project tried to use files, but file access is not enabled for this project."
            )

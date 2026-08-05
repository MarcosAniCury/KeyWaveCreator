from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import ClassVar

import pytest
from PySide6.QtGui import QImage

from keywave_creator.presentation import app as app_module


def test_packaged_application_icon_is_a_large_square_png() -> None:
    icon_resource = files("keywave_creator.presentation").joinpath("assets", "keywave-app-icon.png")
    icon_bytes = icon_resource.read_bytes()
    icon_image = QImage.fromData(icon_bytes)

    assert icon_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert not icon_image.isNull()
    assert icon_image.width() == icon_image.height()
    assert icon_image.width() >= 256


def test_gui_assigns_the_packaged_icon_before_loading_qml(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_icon = object()

    class FakeSignal:
        def connect(self, callback: object) -> None:
            self.callback = callback

    class FakeApplication:
        window_icon: ClassVar[object | None] = None

        @classmethod
        def instance(cls) -> None:
            return None

        def __init__(self, arguments: list[str]) -> None:
            self.arguments = arguments
            self.aboutToQuit = FakeSignal()

        def setApplicationName(self, name: str) -> None:
            self.name = name

        def setApplicationVersion(self, version: str) -> None:
            self.version = version

        def setOrganizationName(self, organization: str) -> None:
            self.organization = organization

        @classmethod
        def setWindowIcon(cls, icon: object) -> None:
            cls.window_icon = icon

        def exec(self) -> int:
            return 37

    class FakeRootContext:
        def setContextProperty(self, name: str, value: object) -> None:
            self.property = (name, value)

    class FakeEngine:
        def __init__(self) -> None:
            self.context = FakeRootContext()

        def rootContext(self) -> FakeRootContext:
            return self.context

        def load(self, url: object) -> None:
            self.url = url

        def rootObjects(self) -> list[object]:
            return [object()]

    class FakeController:
        def __init__(self, service_factory: object) -> None:
            self.service_factory = service_factory

        def shutdown(self) -> None:
            return None

    monkeypatch.setattr(app_module, "configure_logging", lambda: Path("creator.log"))
    monkeypatch.setattr(app_module, "QGuiApplication", FakeApplication)
    monkeypatch.setattr(app_module, "QQmlApplicationEngine", FakeEngine)
    monkeypatch.setattr(app_module, "CreatorController", FakeController)
    monkeypatch.setattr(app_module, "_load_application_icon", lambda: expected_icon)

    assert app_module.run_gui() == 37
    assert FakeApplication.window_icon is expected_icon

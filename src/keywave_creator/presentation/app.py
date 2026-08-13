"""QML application bootstrap and diagnostic logging."""

from __future__ import annotations

import logging
import sys
from importlib.resources import as_file, files
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING

from platformdirs import user_log_path
from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication, QIcon, QPixmap
from PySide6.QtQml import QQmlApplicationEngine

from keywave_creator import __version__

from .controller import CreatorController

if TYPE_CHECKING:
    from keywave_creator.application.service import CreatorService


def configure_logging() -> Path:
    """Configure bounded diagnostic logging and return the active log path."""
    log_directory = user_log_path("KeyWave Creator", "KeyWave", ensure_exists=True)
    log_path = log_directory / "creator.log"
    handler = RotatingFileHandler(
        log_path,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return log_path


def _load_application_icon() -> QIcon:
    """Load the packaged icon without relying on an extracted file's lifetime."""
    icon_resource = files("keywave_creator.presentation").joinpath("assets", "keywave-app-icon.png")
    icon_pixmap = QPixmap()
    if not icon_pixmap.loadFromData(icon_resource.read_bytes()):
        raise RuntimeError("The packaged KeyWave Creator icon is not a valid PNG image.")
    return QIcon(icon_pixmap)


def run_gui() -> int:
    """Launch the English Neon Wave Creator interface."""
    configure_logging()
    application = QGuiApplication.instance() or QGuiApplication(sys.argv)
    application.setApplicationName("KeyWave Creator")
    application.setApplicationVersion(__version__)
    application.setOrganizationName("KeyWave")
    try:
        QGuiApplication.setWindowIcon(_load_application_icon())
    except (OSError, RuntimeError):
        logging.getLogger(__name__).exception("Packaged application icon could not be loaded")
        raise

    engine = QQmlApplicationEngine()
    from keywave_creator.infrastructure.bootstrap import create_default_source_expander
    from keywave_creator.infrastructure.spotify import SpotifyPkceSession

    spotify_session = SpotifyPkceSession()
    controller = CreatorController(
        _create_service,
        source_expander_factory=lambda: create_default_source_expander(spotify_session),
    )
    engine.rootContext().setContextProperty("creatorController", controller)
    application.aboutToQuit.connect(controller.shutdown)
    qml_resource = files("keywave_creator.presentation").joinpath("qml", "Main.qml")
    with as_file(qml_resource) as qml_path:
        engine.load(QUrl.fromLocalFile(str(qml_path)))
        if not engine.rootObjects():
            logging.getLogger(__name__).error("QML root object could not be loaded")
            return 1
        return application.exec()


def _create_service() -> CreatorService:
    from keywave_creator.infrastructure.bootstrap import create_default_service

    return create_default_service()

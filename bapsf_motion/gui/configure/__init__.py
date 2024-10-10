__all__ = ["ConfigureGUI"]
from bapsf_motion.gui.configure.configure_ import ConfigureGUI

if __name__ == "__main__":
    import argparse
    import pathlib

    from PySide6.QtWidgets import QApplication

    parser = argparse.ArgumentParser(
        description="Launch the bapsf_motion Configuration GUI (ConfigureGUI)",
    )
    parser.add_argument(
        "-d",
        "--defaults-file",
        help="Path to the TOML defaults file that contains pre-defined configurations.",
        default=(pathlib.Path.cwd() / "bapsf_motion.toml").resolve(),
        type=pathlib.Path
    )
    parser.add_argument(
        "-c",
        "--config-file",
        help="Path to a TOML run configuration file",
        default=None,
        type=pathlib.Path,
    )
    args = parser.parse_args()

    if not args.defaults_file.exists():
        args.defaults_file = None

    if args.config_file is not None and not args.config_file.exists():
        args.config_file = None

    app = QApplication([])

    window = ConfigureGUI(
        config=args.config_file,
        defaults=args.defaults_file,
    )
    window.show()

    app.exec()

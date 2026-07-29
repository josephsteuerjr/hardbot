from pathlib import Path


ROOT = Path(__file__).parents[1]
PULT = ROOT / "pult"


def test_only_supported_tk_pult_is_shipped():
    assert (PULT / "pult.py").is_file()
    assert (PULT / "Пульт.bat").is_file()
    assert not (PULT / "dist").exists()
    assert not any(
        path.is_file() for path in (PULT / "src-tauri").rglob("*")
    )

    launcher = (PULT / "Пульт.bat").read_text("utf-8")
    assert "pythonw pult.py" in launcher
    assert "tauri" not in launcher.casefold()

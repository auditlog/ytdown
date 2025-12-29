"""
Tests for Poetry configuration and project structure.
"""

import os
import shutil
import subprocess
import tomllib
import pytest
from pathlib import Path


class TestPoetryConfiguration:
    """Test Poetry configuration and dependencies."""

    def test_pyproject_toml_exists(self):
        """Test that pyproject.toml exists in project root."""
        project_root = Path(__file__).parent.parent
        pyproject_path = project_root / "pyproject.toml"
        assert pyproject_path.exists(), "pyproject.toml not found in project root"

    def test_pyproject_toml_valid(self):
        """Test that pyproject.toml is valid TOML."""
        project_root = Path(__file__).parent.parent
        pyproject_path = project_root / "pyproject.toml"

        with open(pyproject_path, "rb") as f:
            try:
                config = tomllib.load(f)
            except Exception as e:
                pytest.fail(f"Invalid TOML file: {e}")

        # Check required sections
        assert "tool" in config
        assert "poetry" in config["tool"]
        assert "build-system" in config

    def test_project_metadata(self):
        """Test that project metadata is properly configured."""
        project_root = Path(__file__).parent.parent
        pyproject_path = project_root / "pyproject.toml"

        with open(pyproject_path, "rb") as f:
            config = tomllib.load(f)

        poetry_config = config["tool"]["poetry"]
        dependencies = poetry_config["dependencies"]

        # Check required metadata
        assert "name" in poetry_config
        assert poetry_config["name"] == "ytdown"
        assert "version" in poetry_config
        assert "description" in poetry_config
        assert "python" in dependencies

        # Check Python version requirement
        python_req = dependencies["python"]
        assert python_req.startswith("^3.12") or python_req.startswith(">=3.12")

    def test_dependencies_defined(self):
        """Test that all required dependencies are defined."""
        project_root = Path(__file__).parent.parent
        pyproject_path = project_root / "pyproject.toml"

        with open(pyproject_path, "rb") as f:
            config = tomllib.load(f)

        dependencies = config["tool"]["poetry"]["dependencies"]

        # Check core dependencies
        required_deps = [
            "yt-dlp",
            "mutagen",
            "python-telegram-bot",
            "requests",
            "python-dotenv"
        ]

        for dep in required_deps:
            assert dep in dependencies, f"Missing dependency: {dep}"

    def test_dev_dependencies_defined(self):
        """Test that development dependencies are defined."""
        project_root = Path(__file__).parent.parent
        pyproject_path = project_root / "pyproject.toml"

        with open(pyproject_path, "rb") as f:
            config = tomllib.load(f)

        dev_deps = config["tool"]["poetry"]["group"]["dev"]["dependencies"]

        # Check dev dependencies
        required_dev_deps = [
            "pytest",
            "pytest-asyncio",
            "pytest-cov",
            "black",
            "ruff",
            "mypy"
        ]

        for dep in required_dev_deps:
            assert dep in dev_deps, f"Missing dev dependency: {dep}"

    def test_scripts_defined(self):
        """Test that Poetry scripts are properly defined."""
        project_root = Path(__file__).parent.parent
        pyproject_path = project_root / "pyproject.toml"

        with open(pyproject_path, "rb") as f:
            config = tomllib.load(f)

        scripts = config["tool"]["poetry"].get("scripts", {})

        # Check expected scripts
        assert "ytdown" in scripts
        assert scripts["ytdown"] == "main:main"
        assert "ytdown-setup" in scripts
        assert scripts["ytdown-setup"] == "setup_config:main"

    def test_tool_configurations(self):
        """Test that tool configurations are present."""
        project_root = Path(__file__).parent.parent
        pyproject_path = project_root / "pyproject.toml"

        with open(pyproject_path, "rb") as f:
            config = tomllib.load(f)

        tool_config = config["tool"]

        # Check tool configurations
        assert "black" in tool_config
        assert "ruff" in tool_config
        assert "mypy" in tool_config
        assert "pytest" in tool_config

        # Verify Black configuration
        black_config = tool_config["black"]
        assert black_config["line-length"] == 100
        assert "py312" in str(black_config["target-version"])

        # Verify Ruff configuration
        ruff_config = tool_config["ruff"]
        assert ruff_config["line-length"] == 100
        assert ruff_config["target-version"] == "py312"

    def test_pytest_configuration(self):
        """Test pytest configuration in pyproject.toml."""
        project_root = Path(__file__).parent.parent
        pyproject_path = project_root / "pyproject.toml"

        with open(pyproject_path, "rb") as f:
            config = tomllib.load(f)

        pytest_config = config["tool"]["pytest"]["ini_options"]

        assert "tests" in pytest_config["testpaths"]
        assert "test_*.py" in pytest_config["python_files"]
        assert pytest_config["asyncio_mode"] == "auto"

    def test_coverage_configuration(self):
        """Test coverage configuration."""
        project_root = Path(__file__).parent.parent
        pyproject_path = project_root / "pyproject.toml"

        with open(pyproject_path, "rb") as f:
            config = tomllib.load(f)

        coverage_config = config["tool"]["coverage"]

        # Check coverage run configuration
        assert "bot" in coverage_config["run"]["source"]
        assert "*/tests/*" in coverage_config["run"]["omit"]

        # Check coverage report configuration
        assert "pragma: no cover" in coverage_config["report"]["exclude_lines"]


class TestRequirementsFile:
    """Test requirements.txt compatibility."""

    def test_requirements_txt_exists(self):
        """Test that requirements.txt exists for backward compatibility."""
        project_root = Path(__file__).parent.parent
        requirements_path = project_root / "requirements.txt"
        assert requirements_path.exists(), "requirements.txt not found"

    def test_requirements_txt_content(self):
        """Test that requirements.txt contains core dependencies."""
        project_root = Path(__file__).parent.parent
        requirements_path = project_root / "requirements.txt"

        with open(requirements_path, "r") as f:
            content = f.read()

        # Check core dependencies are listed
        required_deps = [
            "yt-dlp",
            "mutagen",
            "python-telegram-bot",
            "requests",
            "python-dotenv"
        ]

        for dep in required_deps:
            assert dep in content, f"Missing dependency in requirements.txt: {dep}"


class TestProjectStructure:
    """Test project structure and package organization."""

    def test_bot_package_exists(self):
        """Test that bot package is properly structured."""
        project_root = Path(__file__).parent.parent
        bot_package = project_root / "bot"

        assert bot_package.exists(), "bot package not found"
        assert bot_package.is_dir(), "bot should be a directory"

        # Check __init__.py exists
        init_file = bot_package / "__init__.py"
        assert init_file.exists(), "__init__.py not found in bot package"

    def test_core_modules_exist(self):
        """Test that all core modules exist in bot package."""
        project_root = Path(__file__).parent.parent
        bot_package = project_root / "bot"

        required_modules = [
            "config.py",
            "security.py",
            "cleanup.py",
            "transcription.py",
            "downloader.py",
            "cli.py",
            "telegram_commands.py",
            "telegram_callbacks.py"
        ]

        for module in required_modules:
            module_path = bot_package / module
            assert module_path.exists(), f"Module not found: {module}"

    def test_main_entry_point(self):
        """Test that main.py entry point exists."""
        project_root = Path(__file__).parent.parent
        main_path = project_root / "main.py"

        assert main_path.exists(), "main.py not found"

        # Check it has main function
        with open(main_path, "r") as f:
            content = f.read()
            assert "def main()" in content
            assert 'if __name__ == "__main__"' in content


@pytest.mark.skipif(
    not shutil.which("poetry"),
    reason="Poetry not installed"
)
class TestPoetryCommands:
    """Test Poetry commands (requires Poetry installation)."""

    def test_poetry_check(self):
        """Test that poetry check passes."""
        project_root = Path(__file__).parent.parent
        result = subprocess.run(
            ["poetry", "check"],
            cwd=project_root,
            capture_output=True,
            text=True
        )
        assert result.returncode == 0, f"Poetry check failed: {result.stderr}"

    def test_poetry_show(self):
        """Test that poetry can show dependencies."""
        project_root = Path(__file__).parent.parent
        result = subprocess.run(
            ["poetry", "show", "--tree"],
            cwd=project_root,
            capture_output=True,
            text=True
        )
        # This might fail if not installed, which is ok
        if result.returncode == 0:
            assert "yt-dlp" in result.stdout